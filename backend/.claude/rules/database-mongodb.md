---
paths:
  - "app/db/**"
  - "app/services/**"
  - "docker-compose.yml"
---

# Database (MongoDB) Rules

## Driver & access

- Motor (async) only — no sync pymongo calls, no Beanie/ODM.
- The client is created in the FastAPI lifespan (`init_mongo` in `app/db/mongo.py`),
  never at import time. Services receive `db` (an `AsyncIOMotorDatabase`) as a
  parameter; routes get it via `DbDep` (`Depends(get_db)`).
- Collection names come from constants in `app/models/user.py`
  (`USERS_COLLECTION`, `REFRESH_TOKENS_COLLECTION`) — never inline strings.

## Document shapes

- `users`: `{_id: <uuid str>, username, email, passwordHash, role, status, createdAt, updatedAt}`
- `refresh_tokens`: `{_id, tokenHash, userId, createdAt, expiresAt, revokedAt, replacedBy?}`

Rules:

- `_id` is a string UUID (`str(uuid.uuid4())`), never an ObjectId.
- `email` and `username` are lowercased on write AND on lookup (plain unique
  indexes are case-sensitive). Normalization happens in the Pydantic validators
  and `user_service` lookups.
- All datetimes are tz-aware UTC (`datetime.now(timezone.utc)`). Naive datetimes
  produce wrong expiries in PyJWT and BSON.

## Indexes

- Indexes are defined ONLY in `ensure_indexes()` (`app/db/mongo.py`) using named
  `IndexModel`s — `create_indexes` is idempotent on startup. Current set:
  unique `users.email`, unique `users.username`, unique `refresh_tokens.tokenHash`,
  `refresh_tokens.userId`, TTL `refresh_tokens.expiresAt` (`expireAfterSeconds=0`).
- TTL deletion lags up to ~60s — always ALSO check `expiresAt > now` in queries/code.

## Concurrency & errors

- Duplicate detection: catch `pymongo.errors.DuplicateKeyError` and branch on
  `exc.details["keyValue"]` (race-proof). Never check-then-insert.
- Atomic claim/rotate pattern (see `token_service.rotate_refresh_token`):
  `find_one_and_update` with the state you require (`revokedAt: None`) in the
  filter — never read-modify-write in two steps.
