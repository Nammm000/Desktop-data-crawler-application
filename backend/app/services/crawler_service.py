"""Runs an agent's script as a Scrapy crawl and persists the results.

Route-facing entry point: `start_agent_crawl` — validates the script,
atomically flips the agent to "Running", broadcasts the status, then spawns
`execute_crawl` as a fire-and-forget asyncio task and returns the updated
agent (the route answers 202). `execute_crawl` drives a per-run
AsyncCrawlerRunner in pure-asyncio mode (TWISTED_REACTOR_ENABLED=False — the
default Twisted reactor would collide with uvicorn's loop), inserts one
`data` doc per crawled page, then flips the agent to "Completed"/"Failed"
and broadcasts the outcome with the crawled data."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from scrapy import Spider
from scrapy.crawler import AsyncCrawlerRunner
from scrapy.http import Response

from app.core.config import Settings, get_settings
from app.models.agent import AGENTS_COLLECTION, AgentFormat, AgentStatus
from app.schemas.data import DataOut
from app.services import connection_manager, data_service

logger = logging.getLogger(__name__)

# In-flight crawls keyed by agent id. Holds strong references to the
# fire-and-forget tasks (the event loop alone keeps only weak refs) and is a
# second, PATCH-proof "already running" guard on top of the status field.
_running_crawls: dict[str, asyncio.Task[None]] = {}


def _first_match(response: Response, xp: str) -> str | None:
    """Evaluate one XPath; None when it matches nothing. Element nodes yield
    their text (XPath string-value) — `//title` must give "Post Beta", not
    the serialized `<title>Post Beta</title>` that plain `.get()` returns."""
    nodes = response.xpath(xp)
    if not nodes:
        return None
    first = nodes[0]
    if isinstance(first.root, str):  # attribute / text node: value itself
        return first.get()
    return first.xpath("string(.)").get()


class AgentScriptSpider(Spider):
    """Crawls the script's `links` and extracts one item per page using the
    field -> [xpath, ...] map. Scrapy instantiates the class fresh for every
    run; the custom kwargs are named parameters here so `Spider.__init__`
    (which turns leftover kwargs into attributes) never sees them."""

    name = "agent_script"

    def __init__(
        self,
        links: list[str] | None = None,
        field_xpaths: dict[str, list[str]] | None = None,
        results: list[dict] | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.start_urls = links or []
        self._field_xpaths = field_xpaths or {}
        self._results = results if results is not None else []

    def parse(self, response: Response):
        fields: dict[str, str | None] = {}
        for field, xpaths in self._field_xpaths.items():
            fields[field] = None
            for xp in xpaths:  # try XPaths in order; first non-empty wins
                try:
                    value = _first_match(response, xp)
                except Exception:
                    # parsel raises on malformed XPath — skip it, don't
                    # kill the whole crawl.
                    continue
                if value is not None and value.strip():
                    fields[field] = value.strip()
                    break
        self._results.append({"url": response.url, "fields": fields})


def _parse_run_script(
    doc: dict, settings: Settings
) -> tuple[list[str], dict[str, list[str]]]:
    """Run-time script validation (stricter than agent_service's write-time
    JSON-parseability check). Returns (links, {field: [xpath, ...]})."""
    if doc["format"] != AgentFormat.JSON:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only agents with format 'json' can be run",
        )
    try:
        parsed = json.loads(doc["script"])
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"script is not valid JSON: {exc.msg}",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="script must be a JSON object mapping field names to "
            "XPath expressions",
        )

    links = parsed.get("links")
    if (
        not isinstance(links, list)
        or not links
        or not all(isinstance(u, str) and u.strip() for u in links)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="script 'links' must be a non-empty list of URL strings",
        )
    # Reject up-front instead of letting CLOSESPIDER_PAGECOUNT truncate
    # silently mid-crawl.
    if len(links) > settings.crawl_max_pages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"script has more than {settings.crawl_max_pages} links "
            "(CRAWL_MAX_PAGES)",
        )

    field_xpaths: dict[str, list[str]] = {}
    for key, value in parsed.items():
        if key == "links":
            continue
        if isinstance(value, str) and value.strip():
            field_xpaths[key] = [value]
        elif (
            isinstance(value, list)
            and value
            and all(isinstance(v, str) and v.strip() for v in value)
        ):
            field_xpaths[key] = value
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"script field '{key}' must be a non-empty XPath "
                "string or a non-empty list of XPath strings",
            )
    return [u.strip() for u in links], field_xpaths


def _scrapy_settings(settings: Settings) -> dict:
    return {
        # Pure asyncio on uvicorn's loop; the default True would install a
        # Twisted reactor that conflicts with the running loop.
        "TWISTED_REACTOR_ENABLED": False,
        # The script is the user's explicit list of pages to fetch; robots
        # enforcement could silently zero results. Flip to True for
        # politeness if desired.
        "ROBOTSTXT_OBEY": False,
        "USER_AGENT": "data-crawler/0.1.0",  # default Scrapy UA gets 403'd
        "CONCURRENT_REQUESTS_PER_DOMAIN": 4,  # gentle on a single site
        "DOWNLOAD_TIMEOUT": 30,  # default 180s stalls dead links too long
        "RETRY_TIMES": 1,
        # Hard ceiling: an agent can never hang in Running; partial results
        # still complete the run.
        "CLOSESPIDER_TIMEOUT": float(settings.crawl_timeout_seconds),
        "CLOSESPIDER_PAGECOUNT": settings.crawl_max_pages,
        "LOG_LEVEL": "WARNING",  # keep crawl chatter out of the API logs
        # Don't attach a Scrapy handler to the root logger (uvicorn owns it).
        "LOG_INSTALL_ROOT_HANDLER": False,
        "TELNETCONSOLE_ENABLED": False,  # never bind a port inside the API
    }


def _status_frame(agent: dict, agent_status: str, **extra) -> dict:
    frame = {
        "type": "agentStatus",
        "agentId": agent["_id"],
        "agentName": agent["name"],
        "status": agent_status,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    frame.update(extra)
    return frame


async def _set_agent_status(
    db: AsyncIOMotorDatabase, agent_id: str, new_status: str, acting_email: str
) -> dict | None:
    """None when the agent was deleted mid-crawl (callers tolerate)."""
    return await db[AGENTS_COLLECTION].find_one_and_update(
        {"_id": agent_id},
        {
            "$set": {
                "status": new_status,
                "updatedAt": datetime.now(timezone.utc),
                "updatedBy": acting_email,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def start_agent_crawl(
    db: AsyncIOMotorDatabase, agent_id: str, acting_email: str
) -> dict:
    """Validate -> atomically claim (status -> Running) -> broadcast -> spawn.
    Raises 404 (unknown id), 409 (already running), 400 (script not runnable)."""
    settings = get_settings()

    in_flight = _running_crawls.get(agent_id)
    if in_flight is not None and not in_flight.done():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent is already running",
        )

    doc = await db[AGENTS_COLLECTION].find_one({"_id": agent_id})
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    links, field_xpaths = _parse_run_script(doc, settings)

    # Atomic claim (same idiom as the refresh-token rotation): exactly one
    # concurrent run request can flip a non-Running agent.
    updated = await db[AGENTS_COLLECTION].find_one_and_update(
        {"_id": agent_id, "status": {"$ne": AgentStatus.RUNNING}},
        {
            "$set": {
                "status": AgentStatus.RUNNING,
                "updatedAt": datetime.now(timezone.utc),
                "updatedBy": acting_email,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        # Lost the race, or deleted between the read and the claim.
        current = await db[AGENTS_COLLECTION].find_one({"_id": agent_id}, {"_id": 1})
        if current is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent is already running",
        )

    await connection_manager.manager.broadcast(
        _status_frame(updated, AgentStatus.RUNNING)
    )

    crawl_task = asyncio.create_task(
        execute_crawl(
            db,
            agent_snapshot={"id": updated["_id"], "name": updated["name"]},
            acting_email=acting_email,
            links=links,
            field_xpaths=field_xpaths,
        )
    )
    _running_crawls[agent_id] = crawl_task
    return updated


async def execute_crawl(
    db: AsyncIOMotorDatabase,
    *,
    agent_snapshot: dict,
    acting_email: str,
    links: list[str],
    field_xpaths: dict[str, list[str]],
) -> None:
    """Background body: crawl -> persist -> status flip -> broadcast. Never
    raises (the Failed path swallows and logs); always deregisters itself."""
    agent_id = agent_snapshot["id"]
    frame_agent = {"_id": agent_id, "name": agent_snapshot["name"]}
    try:
        # Constructed per run inside the running loop, never shared.
        runner = AsyncCrawlerRunner(settings=_scrapy_settings(get_settings()))
        results: list[dict] = []  # the spider appends via this shared ref
        await runner.crawl(
            AgentScriptSpider,
            links=links,
            field_xpaths=field_xpaths,
            results=results,
        )
        docs = data_service.build_data_docs(agent_snapshot, results)
        if docs:  # pymongo rejects insert_many([]) — 0-item runs skip persistence
            await data_service.insert_many(db, docs)
        await _set_agent_status(db, agent_id, AgentStatus.COMPLETED, acting_email)
        await connection_manager.manager.broadcast(
            _status_frame(
                frame_agent,
                AgentStatus.COMPLETED,
                count=len(docs),
                data=[
                    DataOut.from_doc(d).model_dump(mode="json", by_alias=True)
                    for d in docs
                ],
            )
        )
    except Exception:
        logger.exception("Crawl for agent %s failed", agent_id)
        try:  # best-effort failure reporting — never mask the original error
            await _set_agent_status(db, agent_id, AgentStatus.FAILED, acting_email)
            await connection_manager.manager.broadcast(
                _status_frame(
                    frame_agent,
                    AgentStatus.FAILED,
                    message="Crawl failed; see server logs for details",
                )
            )
        except Exception:
            logger.exception(
                "Failed to record crawl failure for agent %s", agent_id
            )
    finally:
        _running_crawls.pop(agent_id, None)


async def reset_interrupted_crawls(db: AsyncIOMotorDatabase) -> None:
    """Startup sweep: a server restart (including uvicorn --reload) kills
    in-flight crawls; agents left in "Running" would 409-lock forever."""
    result = await db[AGENTS_COLLECTION].update_many(
        {"status": AgentStatus.RUNNING},
        {
            "$set": {
                "status": AgentStatus.FAILED,
                "updatedAt": datetime.now(timezone.utc),
            }
        },
    )
    if result.modified_count:
        logger.warning(
            "Reset %d agent(s) stuck in Running to Failed", result.modified_count
        )
