import logging
import uuid
from datetime import datetime, timedelta, timezone

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.config import get_settings
from app.core.security import generate_refresh_token, hash_refresh_token
from app.models.user import REFRESH_TOKENS_COLLECTION

logger = logging.getLogger(__name__)


class RefreshTokenError(Exception):
    """Unknown or expired refresh token."""


class RefreshTokenReuseError(Exception):
    """A revoked (already rotated) refresh token was replayed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expiry(now: datetime) -> datetime:
    return now + timedelta(days=get_settings().refresh_token_expire_days)


async def issue_refresh_token(db: AsyncIOMotorDatabase, user_id: str) -> str:
    """Insert a new refresh token document; return the raw token (shown to the
    client exactly once — only its SHA-256 hash is stored)."""
    now = _now()
    raw = generate_refresh_token()
    await db[REFRESH_TOKENS_COLLECTION].insert_one(
        {
            "_id": str(uuid.uuid4()),
            "tokenHash": hash_refresh_token(raw),
            "userId": user_id,
            "createdAt": now,
            "expiresAt": _expiry(now),
            "revokedAt": None,
        }
    )
    return raw


async def rotate_refresh_token(db: AsyncIOMotorDatabase, raw_token: str) -> tuple[str, str]:
    """Atomically claim a valid refresh token and insert its successor.

    Returns (new_raw_token, user_id). Raises RefreshTokenError for unknown or
    expired tokens, and RefreshTokenReuseError when a revoked token is replayed
    (which also revokes every refresh token of that user — the whole "family").
    """
    now = _now()
    token_hash = hash_refresh_token(raw_token)

    # Generate the successor up front so claiming and replacing happen in one step.
    new_raw = generate_refresh_token()
    new_hash = hash_refresh_token(new_raw)

    # Atomic claim: the `revokedAt: None` filter makes concurrent refreshes with
    # the same token safe — exactly one of them wins.
    doc = await db[REFRESH_TOKENS_COLLECTION].find_one_and_update(
        {"tokenHash": token_hash, "revokedAt": None, "expiresAt": {"$gt": now}},
        {"$set": {"revokedAt": now, "replacedBy": new_hash}},
    )

    if doc is None:
        existing = await db[REFRESH_TOKENS_COLLECTION].find_one({"tokenHash": token_hash})
        if existing is not None:
            if existing.get("revokedAt") is not None:
                # Replay of an already-rotated or logged-out token: burn the family.
                await revoke_all_for_user(db, existing["userId"])
                logger.warning(
                    "Refresh token reuse detected for user %s; all sessions revoked",
                    existing["userId"],
                )
                raise RefreshTokenReuseError()
            # Expired but not yet swept by the TTL monitor.
            raise RefreshTokenError()
        # Unknown token — no revocation cascade, otherwise anyone could DoS a
        # user by sending random strings.
        raise RefreshTokenError()

    await db[REFRESH_TOKENS_COLLECTION].insert_one(
        {
            "_id": str(uuid.uuid4()),
            "tokenHash": new_hash,
            "userId": doc["userId"],
            "createdAt": now,
            "expiresAt": _expiry(now),
            "revokedAt": None,
        }
    )
    return new_raw, doc["userId"]


async def revoke_refresh_token(db: AsyncIOMotorDatabase, raw_token: str) -> None:
    """Idempotently revoke one refresh token (logout). Never raises."""
    await db[REFRESH_TOKENS_COLLECTION].update_one(
        {"tokenHash": hash_refresh_token(raw_token), "revokedAt": None},
        {"$set": {"revokedAt": _now()}},
    )


async def revoke_all_for_user(db: AsyncIOMotorDatabase, user_id: str) -> None:
    await db[REFRESH_TOKENS_COLLECTION].update_many(
        {"userId": user_id, "revokedAt": None},
        {"$set": {"revokedAt": _now()}},
    )
