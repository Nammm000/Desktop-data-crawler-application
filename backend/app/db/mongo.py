from fastapi import FastAPI, Request
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING, IndexModel

from app.core.config import get_settings
from app.models.user import REFRESH_TOKENS_COLLECTION, USERS_COLLECTION


async def init_mongo(app: FastAPI) -> None:
    settings = get_settings()
    client = AsyncIOMotorClient(settings.mongodb_uri)
    # Fail fast on unreachable host / wrong credentials.
    await client.admin.command("ping")
    app.state.mongo_client = client
    app.state.mongo_db = client[settings.mongodb_db]
    await ensure_indexes(app.state.mongo_db)


async def close_mongo(app: FastAPI) -> None:
    client: AsyncIOMotorClient | None = getattr(app.state, "mongo_client", None)
    if client is not None:
        client.close()


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create indexes idempotently on startup (create_indexes is a no-op when
    an index with the same name already exists)."""
    await db[USERS_COLLECTION].create_indexes(
        [
            IndexModel([("email", ASCENDING)], unique=True, name="uq_email"),
            IndexModel([("username", ASCENDING)], unique=True, name="uq_username"),
        ]
    )
    await db[REFRESH_TOKENS_COLLECTION].create_indexes(
        [
            IndexModel([("tokenHash", ASCENDING)], unique=True, name="uq_token_hash"),
            IndexModel([("userId", ASCENDING)], name="idx_user_revokes"),
            # TTL index: MongoDB deletes documents once expiresAt has passed.
            IndexModel(
                [("expiresAt", ASCENDING)], expireAfterSeconds=0, name="ttl_expires_at"
            ),
        ]
    )


def get_db(request: Request) -> AsyncIOMotorDatabase:
    return request.app.state.mongo_db
