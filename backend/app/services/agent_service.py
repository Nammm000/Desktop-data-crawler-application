import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.models.agent import AGENTS_COLLECTION, AgentFormat
from app.schemas.agent import AgentCreate, AgentUpdate


def _validate_script(format_: str, script: str) -> None:
    """When the effective format is json, the effective script must parse.
    xml/md are stored as-is (no parser dependency). Called with the MERGED
    (old ∪ new) values on update so format/script switches are checked together.
    """
    if format_ != AgentFormat.JSON:
        return
    try:
        json.loads(script)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"script is not valid JSON: {exc.msg}",
        ) from exc


async def create_agent(
    db: AsyncIOMotorDatabase, data: AgentCreate, acting_email: str
) -> dict:
    _validate_script(data.format, data.script)
    now = datetime.now(timezone.utc)
    doc = {
        "_id": str(uuid.uuid4()),
        "name": data.name,
        "type": data.type,
        "status": data.status,
        "format": data.format,
        "script": data.script,
        "createdAt": now,
        "updatedAt": now,
        "updatedBy": acting_email,
    }
    try:
        await db[AGENTS_COLLECTION].insert_one(doc)
    except DuplicateKeyError as exc:
        # The unique index is the source of truth (race-proof under concurrent creates).
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Agent name already taken"
        ) from exc
    return doc


async def get_agent(db: AsyncIOMotorDatabase, agent_id: str) -> dict | None:
    return await db[AGENTS_COLLECTION].find_one({"_id": agent_id})


async def list_agents(
    db: AsyncIOMotorDatabase, *, skip: int = 0, limit: int = 50
) -> tuple[list[dict], int]:
    """One page of agents (newest first) plus the total count.
    Sorting ties on createdAt are broken by _id (unique) so pagination
    boundaries stay deterministic.
    """
    cursor = (
        db[AGENTS_COLLECTION]
        .find({})
        .sort([("createdAt", DESCENDING), ("_id", DESCENDING)])
        .skip(skip)
        .limit(limit)
    )
    agents = await cursor.to_list(length=limit)
    total = await db[AGENTS_COLLECTION].count_documents({})
    return agents, total


async def update_agent(
    db: AsyncIOMotorDatabase, agent_id: str, data: AgentUpdate, acting_email: str
) -> dict | None:
    """Partially update an agent; None if it doesn't exist.
    JSON validation runs on the merged (old ∪ new) format/script pair, so
    PATCHing only `format` to json against a stored non-JSON script fails
    with 400. Renaming to a taken name hits uq_name on the update, not at insert.
    """
    current = await db[AGENTS_COLLECTION].find_one({"_id": agent_id})
    if current is None:
        return None
    updates = data.model_dump(exclude_unset=True)
    _validate_script(
        updates.get("format", current["format"]),
        updates.get("script", current["script"]),
    )
    updates["updatedAt"] = datetime.now(timezone.utc)
    updates["updatedBy"] = acting_email
    try:
        return await db[AGENTS_COLLECTION].find_one_and_update(
            {"_id": agent_id},
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Agent name already taken"
        ) from exc


async def delete_agent(db: AsyncIOMotorDatabase, agent_id: str) -> bool:
    """True when a document was removed; False when the id doesn't exist."""
    result = await db[AGENTS_COLLECTION].delete_one({"_id": agent_id})
    return result.deleted_count == 1
