"""Persistence for the `data` collection — written only by crawler runs."""

import uuid
from datetime import datetime, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

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
