import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
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
        "role": UserRole.USER.value,
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
