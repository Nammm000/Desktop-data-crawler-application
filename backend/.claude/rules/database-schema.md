---
paths:
  - "app/db/**"
  - "app/services/**"
  - "app/models/**"
  - "docker-compose.yml"
---

# Database Schema (Full Reference)

## Connection & lifecycle

- Client: `AsyncIOMotorClient(settings.mongodb_uri)` created in `init_mongo()`
  during FastAPI lifespan startup — never at import time. An `admin.command("ping")`
  fails fast on unreachable host / wrong credentials.
- Handles live on `app.state.mongo_client` / `app.state.mongo_db`;
  `get_db(request)` returns the database (wired as `DbDep`).
- Database name: `settings.mongodb_db` (default `data_crawler`).
- URI must include `authSource=admin` when it has credentials — the root user
  lives in the `admin` database (`.env.example`:
  `mongodb://crawler:crawlerpass@localhost:27017/data_crawler?authSource=admin`).

## Collections overview

| Collection | Constant | Written by |
|---|---|---|
| `users` | `USERS_COLLECTION` (`app/models/user.py`) | `user_service.create_user`, `user_service.update_password`, `user_service.list_users` / `update_user_status` / `update_user_role` (admin), `user_service.delete_user` / `delete_users` (admin) |
| `refresh_tokens` | `REFRESH_TOKENS_COLLECTION` | `token_service` (issue / rotate / revoke / delete_for_users) |

```mermaid
erDiagram
    users ||--o{ refresh_tokens : "userId"
    users {
        string _id PK
        string username UK
        string email UK
        string passwordHash
        string role
        string status
        datetime createdAt
        datetime updatedAt
    }
    refresh_tokens {
        string _id PK
        string tokenHash UK
        string userId FK
        datetime createdAt
        datetime expiresAt
        datetime revokedAt
        string replacedBy
    }
```

## `users` document shape

| Field | Type | Set how |
|---|---|---|
| `_id` | str | `str(uuid.uuid4())` — string UUID, **never an ObjectId** |
| `username` | str | from `SignupRequest`; lowercased by the Pydantic validator |
| `email` | str | from `SignupRequest`; lowercased; lookups use `email.lower()` |
| `passwordHash` | str | `hash_password()` — bcrypt, `gensalt(rounds=settings.bcrypt_rounds)` (12) |
| `role` | str | hardcoded `UserRole.ADMIN.value` at signup (see limitations in `project-overview.md`); values `"admin"` \| `"user"` |
| `status` | str | `UserStatus.ACTIVE` at signup; values `"active"` \| `"inactive"` \| `"banned"` — only `active` may authenticate |
| `createdAt` | datetime | tz-aware UTC (`datetime.now(timezone.utc)`) |
| `updatedAt` | datetime | same `now` as `createdAt`; reset by `update_password`, `update_user_status`, `update_user_role` |

`role` is stored as a plain string backed by the `UserRole(str, Enum)` enum;
`status` is a plain string (constants class `UserStatus`, deliberately not an Enum).

## `refresh_tokens` document shape

| Field | Type | Set how |
|---|---|---|
| `_id` | str | `str(uuid.uuid4())` |
| `tokenHash` | str | SHA-256 hexdigest of the raw token (`hash_refresh_token`); the raw token is `secrets.token_urlsafe(48)` (384-bit) and shown to the client exactly once |
| `userId` | str | the user's `_id` string — linkage to `users._id` |
| `createdAt` | datetime | tz-aware UTC |
| `expiresAt` | datetime | `createdAt + timedelta(days=settings.refresh_token_expire_days)` (7 days) |
| `revokedAt` | datetime \| None | `None` at insert; set to `now` on rotation, logout, family revocation |
| `replacedBy` | str | set **only during rotation**: the successor token's SHA-256 hash |

## Indexes (`ensure_indexes()` in `app/db/mongo.py`)

Indexes change only in `ensure_indexes()`. `create_indexes` is idempotent — a no-op
when an index with the same name exists.

| Collection | Index name | Keys | Options | Why it exists (query that uses it) |
|---|---|---|---|---|
| `users` | `uq_email` | `{email: ASCENDING}` | unique | login `find_one({email})`; duplicate-signup 409s (race-proof) |
| `users` | `uq_username` | `{username: ASCENDING}` | unique | duplicate-signup 409s (race-proof) |
| `refresh_tokens` | `uq_token_hash` | `{tokenHash: ASCENDING}` | unique | O(1) atomic token claim / lookup |
| `refresh_tokens` | `idx_user_revokes` | `{userId: ASCENDING}` | — | `update_many` family revocation (reuse detection, password change) |
| `refresh_tokens` | `ttl_expires_at` | `{expiresAt: ASCENDING}` | TTL, `expireAfterSeconds=0` | background deletion of expired tokens |

## Query patterns

`users`:

- `insert_one(doc)` — signup; `DuplicateKeyError` branches on
  `exc.details["keyValue"]` (`email` vs `username`) → distinct 409 message.
  Never check-then-insert.
- `find_one({"email": email.lower()})` — `get_by_email`.
- `find_one({"_id": user_id})` — `get_by_id` (string UUID key).
- `update_one({"_id": user_id}, {"$set": {"passwordHash", "updatedAt"}})` —
  `update_password`.
- `find({}).sort([("createdAt", DESCENDING), ("_id", DESCENDING)]).skip(skip).limit(limit)`
  + `count_documents({})` — admin listing (`list_users`): newest first, `_id`
  tiebreaker keeps pagination deterministic. Two separate queries, so `total` can
  momentarily skew under concurrent signups. In-memory sort by policy — no
  dedicated index until the collection grows (escalation:
  `IndexModel([("createdAt", DESCENDING)], name="idx_users_created_at")`).
- `find_one_and_update({"_id": user_id}, {"$set": {"status" | "role", "updatedAt"}},
  return_document=ReturnDocument.AFTER)` — admin status/role updates; `AFTER`
  returns the post-update doc for the response (default returns the pre-update
  doc — a silent-bug trap).
- `delete_one({"_id": user_id})` / `delete_many({"_id": {"$in": user_ids}})` — admin
  deletion (single / bulk).

`refresh_tokens`:

- `insert_one({...})` — issue (login, change-password) and rotate successor.
- `find_one_and_update({"tokenHash": h, "revokedAt": None, "expiresAt": {"$gt": now}}, {"$set": {"revokedAt": now, "replacedBy": new_hash}})` —
  **atomic rotation claim**. The `revokedAt: None` + `expiresAt > now` filter makes
  concurrent refreshes with the same token race-safe: exactly one winner. Never
  read-modify-write in two steps.
- `find_one({"tokenHash": token_hash})` — fallback lookup to distinguish reuse
  (revoked → family revocation) vs expired-not-yet-swept vs unknown token.
- `update_one({"tokenHash": ..., "revokedAt": None}, {"$set": {"revokedAt": now}})` —
  idempotent single-token logout revoke; unknown token matches nothing (204 anyway).
- `update_many({"userId": user_id, "revokedAt": None}, {"$set": {"revokedAt": now}})` —
  revoke all sessions (reuse detection, password change, inactive/missing user on refresh).
- `delete_many({"userId": {"$in": user_ids}})` — cascade on user deletion (`delete_for_users`):
  tokens are HARD-deleted, so a replayed token reads as unknown → 401 with no cascade
  (vs. ban, which only revokes and keeps the reuse-detection trail).

No projections are used anywhere — field filtering happens at the route boundary via
`UserOut.from_doc` (`passwordHash` dropped, `_id` → `id`).

## TTL & expiry semantics

Expiry is enforced **twice**:

1. **Mongo TTL index** `ttl_expires_at` (`expireAfterSeconds=0`): the TTL monitor
   deletes documents once `expiresAt` has passed. The sweep can lag up to ~60s.
2. **Application-side**: every rotation filter includes `"expiresAt": {"$gt": now}`,
   so an expired-but-not-yet-swept token is still rejected
   (`RefreshTokenError` → 401).

Revocation is **logical**: `revokedAt` is set, physical deletion waits for the TTL
index (revoked tokens are not deleted early). Revoked tokens are kept so replays can
be recognized and trigger family revocation.

Access tokens are separate: JWTs are not stored in Mongo at all.

## MongoDB server (docker-compose.yml)

| Setting | Value |
|---|---|
| Image | `mongo:8.0` (container `data-crawler-mongo`) |
| Port | `27017:27017` |
| Root credentials | `crawler` / `crawlerpass` (local dev only) |
| `MONGO_INITDB_DATABASE` | `data_crawler` |
| Volume | named volume `mongo_data` → `/data/db` |
| Restart policy | `unless-stopped` |
| Healthcheck | `mongosh --quiet --eval "db.adminCommand('ping').ok"` — 10s interval, 5s timeout, 5 retries, 10s start period |

Compose defines **only MongoDB** — the API runs via uvicorn on the host.

## Schema management

No migrations, seeding scripts, or ODM. `ensure_indexes()` at startup is the only
schema-management mechanism; documents are created inline in the services. Inspect
data with:

```bash
docker compose exec mongo mongosh -u crawler -p crawlerpass --authenticationDatabase admin data_crawler
```
