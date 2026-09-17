import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.core.security import hash_password
from app.models.user import USERS_COLLECTION, UserStatus, UserRole
from app.schemas.auth import SignupRequest


async def create_user(db: AsyncIOMotorDatabase, data: SignupRequest) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "_id": str(uuid.uuid4()),
        "username": data.username,
        "email": data.email,
        "passwordHash": hash_password(data.password),
        "role": UserRole.ADMIN.value,
        "status": UserStatus.ACTIVE,
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        await db[USERS_COLLECTION].insert_one(doc)
    except DuplicateKeyError as exc:
        # The unique index is the source of truth (race-proof under concurrent signups).
        key = (exc.details or {}).get("keyValue", {})
        if "email" in key:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Email already registered"
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Username already taken"
        ) from exc
    return doc


async def get_by_email(db: AsyncIOMotorDatabase, email: str) -> dict | None:
    return await db[USERS_COLLECTION].find_one({"email": email.lower()})


async def get_by_id(db: AsyncIOMotorDatabase, user_id: str) -> dict | None:
    return await db[USERS_COLLECTION].find_one({"_id": user_id})


async def update_password(db: AsyncIOMotorDatabase, user_id: str, new_hash: str) -> None:
    await db[USERS_COLLECTION].update_one(
        {"_id": user_id},
        {"$set": {"passwordHash": new_hash, "updatedAt": datetime.now(timezone.utc)}},
    )


async def list_users(
    db: AsyncIOMotorDatabase, *, skip: int = 0, limit: int = 50
) -> tuple[list[dict], int]:
    """One page of users (newest first) plus the total count.

    Sorting ties on createdAt are broken by _id (unique) so pagination
    boundaries stay deterministic.
    """
    cursor = (
        db[USERS_COLLECTION]
        .find({})
        .sort([("createdAt", DESCENDING), ("_id", DESCENDING)])
        .skip(skip)
        .limit(limit)
    )
    users = await cursor.to_list(length=limit)
    total = await db[USERS_COLLECTION].count_documents({})
    return users, total


async def update_user_status(
    db: AsyncIOMotorDatabase, user_id: str, new_status: str
) -> dict | None:
    """Atomically set `status` and bump updatedAt; None if the user doesn't exist."""
    return await db[USERS_COLLECTION].find_one_and_update(
        {"_id": user_id},
        {"$set": {"status": new_status, "updatedAt": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )


async def update_user_role(
    db: AsyncIOMotorDatabase, user_id: str, new_role: str
) -> dict | None:
    """Atomically set `role` and bump updatedAt; None if the user doesn't exist."""
    return await db[USERS_COLLECTION].find_one_and_update(
        {"_id": user_id},
        {"$set": {"role": new_role, "updatedAt": datetime.now(timezone.utc)}},
        return_document=ReturnDocument.AFTER,
    )


async def delete_user(db: AsyncIOMotorDatabase, user_id: str) -> bool:
    """True when a document was removed; False when the id doesn't exist."""
    result = await db[USERS_COLLECTION].delete_one({"_id": user_id})
    return result.deleted_count == 1


async def delete_users(db: AsyncIOMotorDatabase, user_ids: list[str]) -> int:
    """Delete every user whose _id is in the list; return how many were removed."""
    result = await db[USERS_COLLECTION].delete_many({"_id": {"$in": user_ids}})
    return result.deleted_count
