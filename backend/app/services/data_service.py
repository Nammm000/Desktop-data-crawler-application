"""Persistence for the `data` collection — written by crawler runs, read by
the per-agent data listing, deleted by the data endpoints."""

import uuid
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING

from app.models.data import DATA_COLLECTION


def build_data_docs(agent: dict, results: list[dict]) -> list[dict]:
    """Map spider results ({"url", "fields"}) to `data` documents. Pure, no
    I/O. `agent` is the at-run-start snapshot: {"id", "name"}."""
    now = datetime.now(timezone.utc)
    return [
        {
            "_id": str(uuid.uuid4()),
            "agentId": agent["id"],
            "agentName": agent["name"],
            "url": item["url"],
            "fields": item["fields"],
            "crawledAt": now,
        }
        for item in results
    ]


async def insert_many(db: AsyncIOMotorDatabase, docs: list[dict]) -> list[dict]:
    """Bulk-insert crawl results. The caller guards the empty case — Motor
    raises InvalidOperation on insert_many([])."""
    await db[DATA_COLLECTION].insert_many(docs)
    return docs


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


async def delete_one(db: AsyncIOMotorDatabase, data_id: str) -> bool:
    """True when a document was removed; False when the id doesn't exist."""
    result = await db[DATA_COLLECTION].delete_one({"_id": data_id})
    return result.deleted_count == 1


async def delete_many(db: AsyncIOMotorDatabase, data_ids: list[str]) -> int:
    """Delete every data doc whose _id is in the list; return how many were
    removed (unknown ids simply don't count)."""
    result = await db[DATA_COLLECTION].delete_many({"_id": {"$in": data_ids}})
    return result.deleted_count
