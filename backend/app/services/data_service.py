"""Persistence for the `data` collection — written by crawler runs (one
insert per document as the crawl produces it, so each doc can be streamed to
the requesting client right after its save), read by the per-agent data
listing and the orphaned-data listing, agent-detached (agentId -> null) on
agent deletion and by the startup sweep, deleted by the data endpoints."""

import logging
import uuid
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING

from app.models.agent import AGENTS_COLLECTION
from app.models.data import DATA_COLLECTION

logger = logging.getLogger(__name__)


def build_data_doc(agent: dict, item: dict) -> dict:
    """Map one spider result ({"url", "fields"}) to a `data` document. Pure,
    no I/O. `agent` is the at-run-start snapshot: {"id", "name"} — the
    snapshot agentId is nulled later if the agent is deleted
    (detach_from_agent). Each doc gets its own crawledAt (docs are saved as
    the crawl produces them); sort ties break by _id."""
    return {
        "_id": str(uuid.uuid4()),
        "agentId": agent["id"],
        "agentName": agent["name"],
        "url": item["url"],
        "fields": item["fields"],
        "crawledAt": datetime.now(timezone.utc),
    }


async def insert_one(db: AsyncIOMotorDatabase, doc: dict) -> None:
    """Persist one crawl result (saved per-document so the run can stream
    each doc to the requesting client right after its save)."""
    await db[DATA_COLLECTION].insert_one(doc)


async def list_by_agent(
    db: AsyncIOMotorDatabase, agent_id: str, *, skip: int = 0, limit: int = 50
) -> tuple[list[dict], int]:
    """One page of crawled-data docs for an agent (newest first) plus the
    total count. Served by idx_agent_crawled; ties on crawledAt (one run
    stamps all its docs with the same now) are broken by _id so pagination
    boundaries stay deterministic — same idiom as agent_service.list_agents."""
    filter_ = {"agentId": agent_id}
    cursor = (
        db[DATA_COLLECTION]
        .find(filter_)
        .sort([("crawledAt", DESCENDING), ("_id", DESCENDING)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    total = await db[DATA_COLLECTION].count_documents(filter_)
    return docs, total


async def list_orphaned(
    db: AsyncIOMotorDatabase, *, skip: int = 0, limit: int = 50
) -> tuple[list[dict], int]:
    """One page of crawled-data docs whose agent no longer exists (newest
    first) plus the total count. Deleting an agent nulls agentId on its docs
    (agent_service.delete_agent -> detach_from_agent), so orphans are exactly
    the docs with agentId null (equality also matches a missing field, which
    never occurs — every doc is inserted with one). Unlike the $nin anti-join
    this replaces, the null equality is served by idx_agent_crawled.
    agentName survives for display; a recreated same-name agent never relinks
    (the new agent gets a fresh _id and null is never re-populated). Ties on
    crawledAt break by _id, same idiom as list_by_agent."""
    filter_ = {"agentId": None}
    cursor = (
        db[DATA_COLLECTION]
        .find(filter_)
        .sort([("crawledAt", DESCENDING), ("_id", DESCENDING)])
        .skip(skip)
        .limit(limit)
    )
    docs = await cursor.to_list(length=limit)
    total = await db[DATA_COLLECTION].count_documents(filter_)
    return docs, total


async def delete_one(db: AsyncIOMotorDatabase, data_id: str) -> bool:
    """True when a document was removed; False when the id doesn't exist."""
    result = await db[DATA_COLLECTION].delete_one({"_id": data_id})
    return result.deleted_count == 1


async def delete_many(db: AsyncIOMotorDatabase, data_ids: list[str]) -> int:
    """Delete every data doc whose _id is in the list; return how many were
    removed (unknown ids simply don't count)."""
    result = await db[DATA_COLLECTION].delete_many({"_id": {"$in": data_ids}})
    return result.deleted_count


async def detach_from_agent(db: AsyncIOMotorDatabase, agent_id: str) -> int:
    """Null agentId on every data doc of one agent (soft orphaning); return
    how many were detached. agentName stays — the orphaned listing shows
    "crawled by <name> (agent deleted)". Called by agent_service.delete_agent
    and by crawler_service.execute_crawl when the agent vanished mid-run;
    docs inserted concurrently after this runs are caught by the mid-crawl
    detach or the detach_dangling_agents startup sweep."""
    result = await db[DATA_COLLECTION].update_many(
        {"agentId": agent_id}, {"$set": {"agentId": None}}
    )
    return result.modified_count


async def detach_dangling_agents(db: AsyncIOMotorDatabase) -> int:
    """Startup sweep: null agentId on data docs whose agent id matches no
    live agent — pre-cascade leftovers plus the crash window between a
    crawl's insert and its mid-crawl detach. Idempotent: steady state matches
    nothing ($ne: None keeps already-orphaned docs — and a fresh install's
    whole collection — out of the write set). $nin cannot use
    idx_agent_crawled; accepted as a startup-only scan (the per-request
    anti-join list_orphaned used to run scanned on every orphaned listing)."""
    agent_ids = await db[AGENTS_COLLECTION].distinct("_id")
    result = await db[DATA_COLLECTION].update_many(
        {"agentId": {"$nin": agent_ids, "$ne": None}},
        {"$set": {"agentId": None}},
    )
    if result.modified_count:
        logger.warning(
            "Detached %d data doc(s) from deleted agents", result.modified_count
        )
    return result.modified_count
