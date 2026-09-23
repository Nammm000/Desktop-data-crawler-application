import json
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.models.agent import AGENTS_COLLECTION, AgentFormat, AgentSource
from app.schemas.agent import AgentCreate, AgentUpdate
from app.services import agent_secret_service, data_service


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
        "sourceType": data.source_type,
        "status": data.status,
        "format": data.format,
        "script": data.script,
        "hasCookies": False,
        "hasProxies": False,
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
    # by_alias: the wire is camelCase and so is the stored doc — e.g.
    # source_type must land as `sourceType` (name/type/status/format/script
    # are identical either way).
    updates = data.model_dump(exclude_unset=True, by_alias=True)
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
    """True when a document was removed; False when the id doesn't exist.
    Deleting soft-orphans the agent's crawled data: agentId is nulled on
    every data doc (agentName survives for display) so the docs keep
    showing up in data_service.list_orphaned. Delete first, then detach —
    docs inserted by a crawl finishing concurrently land before the detach
    and are caught by it; anything slipping past is healed by the
    detach_dangling_agents startup sweep."""
    result = await db[AGENTS_COLLECTION].delete_one({"_id": agent_id})
    if result.deleted_count != 1:
        return False
    # Stored credentials (if any) go with the agent — never dangle secrets.
    await agent_secret_service.delete_for_agent(db, agent_id)
    await data_service.detach_from_agent(db, agent_id)
    return True
