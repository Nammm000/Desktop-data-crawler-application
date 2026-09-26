"""Runs an agent's script as a Scrapy crawl and persists the results.

Route-facing entry point: `start_agent_crawl` — validates the script,
atomically flips the agent to "Running", broadcasts the status, then spawns
`execute_crawl` as a fire-and-forget asyncio task and returns the updated
agent. The /run route consumes the run as SSE (`sse_events`): documents are
inserted one by one as the crawl produces them and each saved doc is pushed
to every registered listener queue immediately, so the requesting client
sees records stream in live. `execute_crawl` drives a per-run
AsyncCrawlerRunner in pure-asyncio mode (TWISTED_REACTOR_ENABLED=False — the
default Twisted reactor would collide with uvicorn's loop), nulls the saved
docs' agentId when the agent was deleted mid-run, then flips the agent to
"Completed"/"Failed" and broadcasts the outcome with the crawled data.

source_pages agents run in two phases inside `execute_crawl`: a Playwright
listing-page discovery first (source_pages_discovery.discover_links —
collects article links, clicks next_page/load_more buttons, randomized
delays, stop-aware), then the discovered links go through the regular
AgentScriptSpider crawl as if the script had listed them explicitly."""

import asyncio
import dataclasses
import json
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from scrapy import Spider
from scrapy.crawler import AsyncCrawlerRunner
from scrapy.http import Request, Response

from app.core.config import Settings, get_settings
from app.models.agent import AGENTS_COLLECTION, AgentFormat, AgentSource, AgentStatus
from app.models.crawl import (
    MAX_RECORDED_FAILURES,
    FailureReason,
    LastRunOutcome,
)
from app.schemas.data import DataOut
from app.services import (
    agent_secret_service,
    connection_manager,
    data_service,
)
from app.services.facebook_spider import (
    FacebookPostSpider,
    facebook_settings,
    is_facebook_url,
)
from app.services.source_pages_discovery import (
    SourcePagesPlan,
    discover_links,
    parse_source_pages_script,
    source_pages_settings,
)
from app.services.ecommerce_spider import (
    EcommercePlan,
    EcommerceProductSpider,
    ecommerce_settings,
    parse_ecommerce_script,
)

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class _RunningCrawl:
    """One in-flight run. `runner` is attached as soon as execute_crawl
    builds it, so a stop request can reach the crawler; `stop_requested`
    tells the finishing execute_crawl that Stopped (not Completed) is the
    right terminal status. `listeners` holds the SSE event queues of clients
    streaming this run (the /run route registers its queue via
    start_agent_crawl BEFORE the crawl task is spawned, so no document event
    can be missed)."""

    task: asyncio.Task[None]
    runner: AsyncCrawlerRunner | None = None
    stop_requested: bool = False
    listeners: list[asyncio.Queue] = dataclasses.field(default_factory=list)


# In-flight crawls keyed by agent id. Holds strong references to the
# fire-and-forget tasks (the event loop alone keeps only weak refs) and is a
# second, PATCH-proof "already running" guard on top of the status field.
_running_crawls: dict[str, _RunningCrawl] = {}


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
    (which turns leftover kwargs into attributes) never sees them.

    Successful extractions land in `results`; per-link failures (HTTP
    errors, DNS, timeouts, selector-matched-nothing) land in `failures` —
    both lists are caller-owned."""

    name = "agent_script"

    def __init__(
        self,
        links: list[str] | None = None,
        field_xpaths: dict[str, list[str]] | None = None,
        results: list[dict] | None = None,
        failures: list[dict] | None = None,
        on_result=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.start_urls = links or []
        self._field_xpaths = field_xpaths or {}
        self._results = results if results is not None else []
        self._failures = failures if failures is not None else []
        self._on_result = on_result

    async def start(self):
        # Explicit requests (not the plain start_urls default) so every link
        # carries an errback — without one, a 404/DNS failure is logged and
        # silently dropped, with no record anywhere. (Scrapy 2.13+ replaced
        # start_requests with this async start() method.)
        for url in self.start_urls:
            yield Request(url, errback=self._on_error)

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
        if all(value is None for value in fields.values()):
            # 200 OK but nothing matched: usually a login wall or a markup
            # change. The doc still lands (all-null fields, same as before)
            # — this entry is the canary that says the selectors went stale.
            self._failures.append(
                {
                    "url": response.url,
                    "reason": FailureReason.UNEXPECTED_HTML,
                    "detail": "page fetched but no XPath matched any field",
                }
            )
        item = {"url": response.url, "fields": fields}
        self._results.append(item)
        if self._on_result is not None:
            self._on_result(item)

    def _on_error(self, failure):
        """Errback for every link: classify the download failure so the run
        summary can say WHY each missing page is missing."""
        request = getattr(failure, "request", None)
        url = getattr(request, "url", None)
        # Under the asyncio runner the errback still receives a Twisted-style
        # Failure; duck-type to the underlying exception either way.
        exc = getattr(failure, "value", failure)
        reason, detail = _classify_download_error(exc)
        self._failures.append({"url": url, "reason": reason, "detail": detail})


def _classify_download_error(
    exc: BaseException, *, via_proxy: bool = False
) -> tuple[str, str | None]:
    """Map a Scrapy download exception to (FailureReason, human detail).

    Classified by exception class NAME: the exceptions live in different
    modules across Scrapy versions (and some are Twisted's), so duck-typing
    the name is more stable than importing each class. The asyncio-mode
    handler wraps several as `Download<Name>` — strip the prefix first."""

    name = type(exc).__name__
    if name.startswith("Download") and len(name) > len("Download"):
        # e.g. DownloadConnectionRefusedError -> ConnectionRefusedError
        name = name[len("Download") :]
    if name == "HttpError":
        http_status = getattr(getattr(exc, "response", None), "status", None)
        if http_status == 404:
            return FailureReason.BROKEN_LINK, None
        if http_status == 429:
            return FailureReason.RATE_LIMITED, None
        return FailureReason.HTTP_ERROR, f"HTTP {http_status}"
    if name in ("IgnoreRequest", "CancelledError"):
        return FailureReason.CANCELLED, None
    if name in ("TimeoutError", "TCPTimedOutError", "DownloadTimeoutError",
                "ServerTimeoutError", "AsyncTimeoutError", "TimeoutException"):
        return FailureReason.TIMEOUT, None
    if name in ("DNSLookupError", "CannotResolveHostError"):
        # the aiohttp handler (asyncio mode) raises its own exception class
        return FailureReason.DNS_ERROR, None
    if name in ("ProxyError", "ClientProxyConnectionError", "ProxyConnectionError"):
        return FailureReason.PROXY_ERROR, str(exc) or None
    if name in ("ConnectionRefusedError", "ConnectionLost", "ConnectionDone",
                "ConnectionResetError", "TunnelError", "ClientConnectorError",
                "CannotConnectError", "ServerDisconnectedError",
                "ClientOSError", "ConnectError"):
        # A connection-level failure THROUGH a proxy is, practically always,
        # the proxy being unreachable/dead.
        if via_proxy:
            return FailureReason.PROXY_ERROR, name
        return FailureReason.CONNECTION_ERROR, name
    return FailureReason.REQUEST_ERROR, f"{name}: {exc}" if str(exc) else name


def _load_json_script(doc: dict) -> dict:
    """Shared run-time prologue: the effective format must be json and the
    script must parse into a dict (source_pages scripts stop here and
    continue in parse_source_pages_script)."""
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
    return parsed


def _parse_run_script(
    doc: dict, settings: Settings
) -> tuple[list[str], dict[str, list[str]]]:
    """Run-time script validation (stricter than agent_service's write-time
    JSON-parseability check). Returns (links, {field: [xpath, ...]}).
    Facebook agents get stricter link rules (https + facebook.com) — their
    field XPaths are OVERRIDES on the spider's built-ins, not the whole set."""
    source = doc.get("sourceType", AgentSource.GENERIC)
    parsed = _load_json_script(doc)

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
    # Scheme allowlist: Scrapy's default handlers also fetch file:, data:,
    # ftp: and s3: — a script link like file:///etc/passwd would read the
    # server's local files into stored data.
    for u in links:
        url = urlparse(u.strip())
        if url.scheme not in ("http", "https") or not url.hostname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"link '{u}' is not a supported http/https URL",
            )
        if source == AgentSource.FACEBOOK and not is_facebook_url(u.strip()):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"link '{u}' is not a facebook.com https URL",
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


def _parse_source_pages_run_script(
    doc: dict, settings: Settings
) -> SourcePagesPlan:
    """source_pages twin of _parse_run_script: structure via
    parse_source_pages_script, then the same URL allowlist / page-cap checks
    applied to the source pages (article links are discovered at runtime and
    are capped there instead)."""
    plan = parse_source_pages_script(_load_json_script(doc))
    for u in plan.source_pages:
        url = urlparse(u)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"source page '{u}' is not a supported http/https URL",
            )
    if len(plan.source_pages) > settings.crawl_max_pages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"script has more than {settings.crawl_max_pages} source "
            "pages (CRAWL_MAX_PAGES)",
        )
    return plan


def _parse_ecommerce_run_script(doc: dict, settings: Settings) -> EcommercePlan:
    """ecommerce twin of _parse_run_script: structure via
    parse_ecommerce_script, then the URL allowlist / page-cap checks on the
    seed listing pages, the ECOMMERCE_MAX_PRODUCTS ceiling on the script's
    own cap, and the default cap fill (product links are discovered at
    runtime and are capped by the spider instead)."""
    plan = parse_ecommerce_script(_load_json_script(doc))
    for u in plan.seeds:
        url = urlparse(u)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"link '{u}' is not a supported http/https URL",
            )
    if len(plan.seeds) > settings.crawl_max_pages:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"script has more than {settings.crawl_max_pages} links "
            "(CRAWL_MAX_PAGES)",
        )
    if plan.max_products is not None and plan.max_products > settings.ecommerce_max_products:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"script 'max_products' cannot exceed "
            f"{settings.ecommerce_max_products} (ECOMMERCE_MAX_PRODUCTS)",
        )
    # None (no script cap) -> the settings ceiling; a lower script cap wins.
    max_products = (
        plan.max_products
        if plan.max_products is not None
        else settings.ecommerce_max_products
    )
    return dataclasses.replace(plan, max_products=max_products)


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
        # Defense in depth behind the run-time scheme check: unregister the
        # non-http handlers entirely so nothing can fetch local files even
        # if a check is bypassed.
        "DOWNLOAD_HANDLERS": {"file": None, "data": None, "ftp": None, "s3": None},
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


# Seconds of stream silence before sse_events emits a keep-alive comment
# (holds proxies / load balancers open during long crawls).
_SSE_KEEPALIVE_SECONDS = 15.0


def _push_event(agent_id: str, event: dict) -> None:
    """Deliver one SSE event to every live listener of the run. Listeners
    are plain in-memory queues on the registry entry — put_nowait is safe on
    the single event loop, and a run is bounded by CRAWL_MAX_PAGES so an
    un-consumed queue cannot grow without limit."""
    entry = _running_crawls.get(agent_id)
    if entry is None:
        return
    for queue in list(entry.listeners):  # snapshot: mutation-safe
        queue.put_nowait(event)


def _done_event(
    frame_agent: dict,
    agent_status: str,
    *,
    runtime_seconds: float,
    last_run: dict | None = None,
    count: int | None = None,
    message: str | None = None,
) -> dict:
    """Terminal SSE event (closes the stream). Same vocabulary as the WS
    terminal frame; lastRun datetimes ride the wire as ISO strings."""
    data: dict = {
        "agentId": frame_agent["_id"],
        "agentName": frame_agent["name"],
        "status": agent_status,
        "runtimeSeconds": runtime_seconds,
    }
    if count is not None:
        data["count"] = count
    if message is not None:
        data["message"] = message
    if last_run is not None:
        data["lastRun"] = {
            **last_run,
            "startedAt": last_run["startedAt"].isoformat(),
            "finishedAt": last_run["finishedAt"].isoformat(),
        }
    return {"event": "done", "data": data}


async def _set_agent_status(
    db: AsyncIOMotorDatabase,
    agent_id: str,
    new_status: str,
    acting_email: str,
    last_run: dict | None = None,
) -> dict | None:
    """None when the agent was deleted mid-crawl (callers tolerate). The
    terminal status flip and the lastRun summary land in ONE update so they
    can never disagree."""
    updates: dict = {
        "status": new_status,
        "updatedAt": datetime.now(timezone.utc),
        "updatedBy": acting_email,
    }
    if last_run is not None:
        updates["lastRun"] = last_run
    return await db[AGENTS_COLLECTION].find_one_and_update(
        {"_id": agent_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )


async def start_agent_crawl(
    db: AsyncIOMotorDatabase,
    agent_id: str,
    acting_email: str,
    *,
    listener: asyncio.Queue | None = None,
) -> dict:
    """Validate -> atomically claim (status -> Running) -> broadcast -> spawn.
    Raises 404 (unknown id), 409 (already running), 400 (script not runnable).
    `listener` (the SSE route's event queue) is registered on the run entry
    BEFORE the crawl task is spawned, so no document event can be missed."""
    settings = get_settings()

    in_flight = _running_crawls.get(agent_id)
    if in_flight is not None and not in_flight.task.done():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Agent is already running",
        )

    doc = await db[AGENTS_COLLECTION].find_one({"_id": agent_id})
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )
    source = doc.get("sourceType", AgentSource.GENERIC)
    plan: SourcePagesPlan | None = None
    ecommerce_plan: EcommercePlan | None = None
    links: list[str] = []
    field_xpaths: dict[str, list[str]] = {}
    if source == AgentSource.SOURCE_PAGES:
        # Article links are discovered at runtime; the plan carries the
        # source pages, pagination mode and field XPaths instead.
        plan = _parse_source_pages_run_script(doc, settings)
        field_xpaths = plan.field_xpaths
    elif source == AgentSource.ECOMMERCE:
        # Product links are discovered at runtime; the plan carries the seed
        # listing pages, pagination/caps and field overrides instead. The
        # seeds ARE the spider's start_urls.
        ecommerce_plan = _parse_ecommerce_run_script(doc, settings)
        links = ecommerce_plan.seeds
        field_xpaths = ecommerce_plan.field_xpaths
    else:
        links, field_xpaths = _parse_run_script(doc, settings)

    cookies: dict[str, str] | None = None
    proxies: list[str] | None = None
    if source == AgentSource.FACEBOOK:
        # Decrypt BEFORE claiming the run: a key problem should fail the
        # request (not the background crawl).
        try:
            cookies, proxies = await agent_secret_service.decrypt_for_run(db, agent_id)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

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
            source=source,
            cookies=cookies,
            proxies=proxies,
            source_pages_plan=plan,
            ecommerce_plan=ecommerce_plan,
        )
    )
    _running_crawls[agent_id] = _RunningCrawl(
        task=crawl_task, listeners=[listener] if listener is not None else []
    )
    return updated


async def sse_events(queue: asyncio.Queue, agent: dict):
    """SSE body for the /run route: a `start` frame, then one `document`
    frame per crawled page as it is saved (queued by the run's saver task),
    keep-alive comments while idle, and the terminal `done` frame that closes
    the stream. `agent` is the freshly claimed agent doc (status Running).
    The finally clause unregisters the queue on any exit, including a client
    disconnect (the generator is cancelled) — the crawl itself is unaffected."""
    agent_id = agent["_id"]

    def _frame(event: str, payload: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"

    try:
        yield _frame(
            "start",
            {"agentId": agent_id, "agentName": agent["name"], "status": AgentStatus.RUNNING},
        )
        while True:
            try:
                event = await asyncio.wait_for(
                    queue.get(), timeout=_SSE_KEEPALIVE_SECONDS
                )
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
                continue
            yield _frame(event["event"], event["data"])
            if event["event"] == "done":
                return
    finally:
        entry = _running_crawls.get(agent_id)
        if entry is not None:
            try:
                entry.listeners.remove(queue)
            except ValueError:
                pass  # already gone (entry replaced or run finished)


async def stop_agent_crawl(
    db: AsyncIOMotorDatabase, agent_id: str, acting_email: str
) -> dict:
    """Ask an in-flight run to wind down gracefully: partial results are
    persisted and the agent lands in Stopped. During a source_pages run's
    discovery phase the runner does not exist yet — stop_requested alone ends
    it (discovery checks it between navigations). Raises 404 (unknown id) /
    409 (not running)."""
    entry = _running_crawls.get(agent_id)
    if entry is None or entry.task.done():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Agent is not running"
        )

    doc = await db[AGENTS_COLLECTION].find_one_and_update(
        {"_id": agent_id},
        {"$set": {"stoppedBy": acting_email}},
        return_document=ReturnDocument.AFTER,
    )
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )

    entry.stop_requested = True
    runner = entry.runner
    if runner is not None:
        # crawler.stop() is the graceful close: in-flight requests settle,
        # then the crawl future completes and execute_crawl continues with
        # whatever results exist.
        for crawler in runner.crawlers:
            crawler.stop()
    return doc


def _build_last_run(
    *,
    started_at: datetime,
    outcome: str,
    total_links: int,
    results: list[dict],
    failures: list[dict],
) -> dict:
    """Assemble the lastRun summary doc written with the status flip."""
    recorded = failures[:MAX_RECORDED_FAILURES]
    return {
        "startedAt": started_at,
        "finishedAt": datetime.now(timezone.utc),
        "outcome": outcome,
        "totalLinks": total_links,
        "successCount": len(results),
        "failureCount": len(failures),
        "failures": recorded,
    }


async def execute_crawl(
    db: AsyncIOMotorDatabase,
    *,
    agent_snapshot: dict,
    acting_email: str,
    links: list[str] | None = None,
    field_xpaths: dict[str, list[str]] | None = None,
    source: str = AgentSource.GENERIC,
    cookies: dict[str, str] | None = None,
    proxies: list[str] | None = None,
    source_pages_plan: SourcePagesPlan | None = None,
    ecommerce_plan: EcommercePlan | None = None,
) -> None:
    """Background body: crawl -> persist (per document, streaming each saved
    doc to the run's SSE listeners) -> status flip -> broadcast. Never raises
    (the Failed path swallows and logs); always deregisters itself.
    source_pages agents: Playwright discovery first (links start unknown),
    then the discovered links go through the regular spider crawl.
    ecommerce agents: the EcommerceProductSpider discovers the product links
    from the seed listing pages itself (links = the seeds)."""
    agent_id = agent_snapshot["id"]
    frame_agent = {"_id": agent_id, "name": agent_snapshot["name"]}
    started_wall = datetime.now(timezone.utc)
    started = time.monotonic()  # monotonic: runtime survives clock adjustments
    links = links if links is not None else []  # source_pages fills this in
    results: list[dict] = []  # the spider appends via this shared ref
    failures: list[dict] = []  # errback + parse classify into this one
    spider_stats: dict[str, int] = {}  # ecommerce discovered/attempted counts
    saved_docs: list[dict] = []  # persisted docs (drives count/data/broadcast)
    # Spider results cross into the async saver through this queue: parse
    # callbacks are sync, so they only put_nowait; the single consumer below
    # preserves save/emit order.
    doc_queue: asyncio.Queue[dict | None] = asyncio.Queue()

    def _on_result(item: dict) -> None:
        doc_queue.put_nowait(item)

    async def _persist_and_stream() -> None:
        """Consume doc_queue: persist each spider result as it lands, then
        stream the SAVED doc to every listener (the requirement: a document
        is sent only after its save). The None sentinel ends the loop."""
        while True:
            item = await doc_queue.get()
            if item is None:
                return
            doc = data_service.build_data_doc(agent_snapshot, item)
            try:
                await data_service.insert_one(db, doc)
            except Exception:
                logger.exception(
                    "Persisting a crawl result for agent %s failed", agent_id
                )
                failures.append(
                    {
                        "url": item["url"],
                        "reason": FailureReason.REQUEST_ERROR,
                        "detail": "saving the crawled page to the database "
                        "failed; it was not streamed",
                    }
                )
                continue
            saved_docs.append(doc)
            payload = DataOut.from_doc(doc).model_dump(mode="json", by_alias=True)
            _push_event(agent_id, {"event": "document", "data": payload})

    settings = get_settings()
    crawl_settings = _scrapy_settings(settings)
    spider_class: type = AgentScriptSpider
    spider_kwargs: dict = {}
    if source == AgentSource.FACEBOOK:
        spider_class = FacebookPostSpider
        crawl_settings = facebook_settings(
            crawl_settings, download_delay=settings.facebook_download_delay
        )
        spider_kwargs = {
            "cookies": cookies,
            "proxies": proxies,
            "fb_host": settings.facebook_host,
        }
    elif source == AgentSource.ECOMMERCE:
        spider_class = EcommerceProductSpider
        crawl_settings = ecommerce_settings(
            crawl_settings,
            download_delay=settings.ecommerce_download_delay,
            concurrent_requests=settings.ecommerce_concurrent_requests,
        )
        spider_kwargs = {
            "product_link_xpaths": ecommerce_plan.product_link_xpaths,
            "next_page_xpaths": ecommerce_plan.next_page_xpaths,
            "max_next": ecommerce_plan.max_next,
            "max_products": ecommerce_plan.max_products,
            "stats": spider_stats,
        }
    try:
        saver = asyncio.create_task(_persist_and_stream())
        entry = _running_crawls.get(agent_id)
        skip_crawl = False
        if source_pages_plan is not None:
            # Phase 1 — listing-page discovery (minutes: randomized delays).
            # should_stop reads the registry entry start_agent_crawl created,
            # so a stop request lands here even before any runner exists.
            deadline = started + settings.source_pages_timeout_seconds
            discovery = await discover_links(
                source_pages_plan,
                settings=settings,
                should_stop=lambda: bool(entry and entry.stop_requested),
                deadline=deadline,
            )
            links = discovery.links
            field_xpaths = source_pages_plan.field_xpaths
            failures.extend(discovery.failures)
            crawl_settings = source_pages_settings(
                crawl_settings,
                download_delay=settings.source_pages_article_download_delay,
                # Discovery already spent part of the overall budget — the
                # crawl phase gets whatever remains (floor keeps the setting
                # valid when discovery overran the deadline).
                timeout_seconds=max(30.0, deadline - time.monotonic()),
            )
            if discovery.stopped or not links:
                # Stopped mid-discovery, or every source page failed — either
                # way there is nothing to crawl; the terminal flip below and
                # the unattempted-links accounting handle both.
                skip_crawl = True
        if not skip_crawl:
            # Constructed per run inside the running loop, never shared.
            runner = AsyncCrawlerRunner(settings=crawl_settings)
            if entry is not None:
                entry.runner = runner  # lets stop_agent_crawl reach the crawler
            await runner.crawl(
                spider_class,
                links=links or [],
                field_xpaths=field_xpaths or {},
                results=results,
                failures=failures,
                on_result=_on_result,
                **spider_kwargs,
            )
        stopped = entry is not None and entry.stop_requested
        if source == AgentSource.ECOMMERCE:
            # `links` are just the seeds — the run's real scope is the
            # discovered products (spider_stats is shared with the spider,
            # so the counts survive even an aborted crawl).
            discovered = spider_stats.get("discovered_products", 0)
            attempted = spider_stats.get("attempted_products", 0)
            total_links = discovered or len(links)
            unattempted = max(0, discovered - attempted)
            if unattempted > 0 and stopped:
                failures.append(
                    {
                        "url": None,
                        "reason": FailureReason.CANCELLED,
                        "detail": f"{unattempted} of {discovered} discovered "
                        "products were not fetched before the stop",
                    }
                )
            elif unattempted > 0:
                # CLOSESPIDER (page/time budget) closed the crawl silently —
                # make the truncation visible in the run summary.
                failures.append(
                    {
                        "url": None,
                        "reason": FailureReason.CANCELLED,
                        "detail": "stopped after the crawl budget "
                        "(CRAWL_MAX_PAGES / CRAWL_TIMEOUT_SECONDS) was "
                        f"exhausted; {unattempted} discovered products "
                        "were not fetched",
                    }
                )
        else:
            total_links = len(links)
            if stopped:
                # Links that were never attempted because the stop came early.
                unattempted = len(links) - len(results) - len(failures)
                if unattempted > 0:
                    failures.append(
                        {
                            "url": None,
                            "reason": FailureReason.CANCELLED,
                            "detail": f"{unattempted} of {len(links)} links "
                            "were not fetched before the stop",
                        }
                    )
        # Drain the saver BEFORE finalizing: every doc must be persisted (and
        # streamed) before the terminal status/events — and before the
        # mid-crawl detach below, or a doc still in the queue would land with
        # a dangling agentId after it.
        doc_queue.put_nowait(None)
        await saver
        outcome = LastRunOutcome.STOPPED if stopped else LastRunOutcome.COMPLETED
        last_run = _build_last_run(
            started_at=started_wall,
            outcome=outcome,
            total_links=total_links,
            results=results,
            failures=failures,
        )
        agent = await _set_agent_status(
            db,
            agent_id,
            AgentStatus.STOPPED if stopped else AgentStatus.COMPLETED,
            acting_email,
            last_run=last_run,
        )
        if agent is None:
            # Deleted mid-crawl: delete_agent's detach ran before these docs
            # were inserted, so they'd keep a dangling string agentId and be
            # invisible to the null-based orphaned listing — detach them now
            # (the startup sweep is the backstop if we crash right here).
            await data_service.detach_from_agent(db, agent_id)
        runtime = round(time.monotonic() - started, 1)
        await connection_manager.manager.broadcast(
            _status_frame(
                frame_agent,
                agent["status"] if agent else outcome,
                runtimeSeconds=runtime,
                count=len(saved_docs),
                lastRun={
                    **last_run,
                    # datetimes over the wire as ISO strings (DataOut payloads
                    # serialize the same way)
                    "startedAt": last_run["startedAt"].isoformat(),
                    "finishedAt": last_run["finishedAt"].isoformat(),
                },
                data=[
                    DataOut.from_doc(d).model_dump(mode="json", by_alias=True)
                    for d in saved_docs
                ],
            )
        )
        _push_event(
            agent_id,
            _done_event(
                frame_agent,
                agent["status"] if agent else outcome,
                runtime_seconds=runtime,
                last_run=last_run,
                count=len(saved_docs),
            ),
        )
    except Exception:
        logger.exception("Crawl for agent %s failed", agent_id)
        try:  # best-effort saver drain — flush docs saved before the crash
            doc_queue.put_nowait(None)
            await saver
        except Exception:
            logger.exception("Draining the crawl saver for agent %s failed", agent_id)
        try:  # best-effort failure reporting — never mask the original error
            failed_run = _build_last_run(
                started_at=started_wall,
                outcome=LastRunOutcome.FAILED,
                # ecommerce: products discovered before the crash, if any
                total_links=(
                    spider_stats.get("discovered_products", 0) or len(links)
                    if source == AgentSource.ECOMMERCE
                    else len(links)
                ),
                results=results,
                failures=failures,
            )
            await _set_agent_status(
                db, agent_id, AgentStatus.FAILED, acting_email, last_run=failed_run
            )
            runtime = round(time.monotonic() - started, 1)
            await connection_manager.manager.broadcast(
                _status_frame(
                    frame_agent,
                    AgentStatus.FAILED,
                    runtimeSeconds=runtime,
                    message="Crawl failed; see server logs for details",
                )
            )
            _push_event(
                agent_id,
                _done_event(
                    frame_agent,
                    AgentStatus.FAILED,
                    runtime_seconds=runtime,
                    last_run=failed_run,
                    message="Crawl failed; see server logs for details",
                ),
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
    now = datetime.now(timezone.utc)
    result = await db[AGENTS_COLLECTION].update_many(
        {"status": AgentStatus.RUNNING},
        {
            "$set": {
                "status": AgentStatus.FAILED,
                "updatedAt": now,
                "lastRun": {
                    "startedAt": now,
                    "finishedAt": now,
                    "outcome": LastRunOutcome.FAILED,
                    "totalLinks": 0,
                    "successCount": 0,
                    "failureCount": 1,
                    "failures": [
                        {
                            "url": None,
                            "reason": FailureReason.CANCELLED,
                            "detail": "server restarted while the run was in flight",
                        }
                    ],
                },
            }
        },
    )
    if result.modified_count:
        logger.warning(
            "Reset %d agent(s) stuck in Running to Failed", result.modified_count
        )
