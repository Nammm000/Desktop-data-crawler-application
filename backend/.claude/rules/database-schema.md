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
| `agents` | `AGENTS_COLLECTION` (`app/models/agent.py`) | `agent_service` (create / get / list / update / delete), `crawler_service` (status flips + `lastRun` on run) |
| `agent_secrets` | `AGENT_SECRETS_COLLECTION` (`app/models/agent.py`) | `agent_secret_service.set_credentials` / `clear_credentials` / `delete_for_agent` (cascade on agent delete); read by `get_metadata` (names/counts) and `decrypt_for_run` (crawl-time, decrypt in memory only) |
| `data` | `DATA_COLLECTION` (`app/models/data.py`) | `data_service.insert_many`, called by `crawler_service` (one doc per crawled page); `data_service.detach_from_agent` (agentId → null on agent deletion + mid-crawl deletion) and `detach_dangling_agents` (startup sweep); read by `data_service.list_by_agent` (`GET /agents/{agentId}/data`) and `data_service.list_orphaned` (`GET /data/orphaned`); deleted by `data_service.delete_one` / `delete_many` (`DELETE /data/{dataId}`, `DELETE /data`) |

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
    agents {
        string _id PK
        string name UK
        string type
        string status
        string format
        string script
        datetime createdAt
        datetime updatedAt
        string updatedBy
    }
    agents ||--o{ data : "agentId"
    data {
        string _id PK
        string agentId FK "nulled on agent delete"
        string agentName
        string url
        object fields
        datetime crawledAt
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

## `agents` document shape

| Field | Type | Set how |
|---|---|---|
| `_id` | str | `str(uuid.uuid4())` — string UUID, **never an ObjectId** |
| `name` | str | from `AgentCreate`; 1–100 chars, whitespace-stripped by the Pydantic validator; unique (`uq_name`) |
| `type` | str | constants class `AgentType` (`app/models/agent.py`), deliberately not an Enum; default `"one_post"` |
| `sourceType` | str | `AgentSource`: `"generic"` (XPath spider, default) \| `"facebook"` (`FacebookPostSpider` with built-in post fields + optional overrides); written on create/PATCH, read by `_parse_run_script` and `execute_crawl` to pick the spider |
| `status` | str | constants class `AgentStatus`; values `"New"` \| `"Running"` \| `"Completed"` \| `"Stopped"` \| `"Failed"`; default `"New"`; terminal flips happen in `execute_crawl` (`Stopped` when a stop was requested — partial data kept) and `reset_interrupted_crawls` (restart sweep → `Failed`) |
| `format` | str | `"json"` \| `"xml"` \| `"md"` (`AgentFormat`) — describes how `script` should be parsed |
| `script` | str | raw text content of a json/xml/md file; 1–1M chars; must parse via `json.loads` when format is `json` — enforced in `agent_service._validate_script` (also on the merged PATCH view) |
| `hasCookies` | bool | set by `agent_secret_service.set_credentials` / `clear_credentials`; display-only flag — the values live (encrypted) in `agent_secrets` |
| `hasProxies` | bool | same as `hasCookies`, proxy side |
| `lastRun` | obj \| absent | written ATOMICALLY with the terminal status flip: `{startedAt, finishedAt, outcome: "Completed"\|"Stopped"\|"Failed", totalLinks, successCount, failureCount, failures: [{url, reason, detail?}]}` (capped at `MAX_RECORDED_FAILURES` = 100; reasons in `app/models/crawl.py`); absent on agents that never ran |
| `stoppedBy` | str | email of the stop requester (`stop_agent_crawl`); survives the run |
| `createdAt` | datetime | tz-aware UTC (`datetime.now(timezone.utc)`) |
| `updatedAt` | datetime | same `now` as `createdAt`; reset by `update_agent` |
| `updatedBy` | str | acting user's email — creator on insert, patcher on update; server-derived from `CurrentUser` (never accepted from the request body) |

## `agent_secrets` document shape

One document per agent WITH stored credentials, keyed by the agent's `_id`
(no separate id). Written by `agent_secret_service`; hard-deleted when the
credentials are cleared or the agent is deleted. **No route ever returns the
plaintext** — it exists only inside `execute_crawl`'s stack frame after
`decrypt_for_run`.

| Field | Type | Set how |
|---|---|---|
| `_id` | str | the agent's `_id` (shared key, upsert) |
| `cookiesEnc` | str | Fernet ciphertext of the raw cookie-header paste (empty string when only proxies are stored) |
| `cookieNames` | list[str] | non-sensitive names (capped at 30) for the edit dialog's metadata endpoint |
| `proxiesEnc` | str | Fernet ciphertext of the newline-separated proxy list (empty when only cookies are stored) |
| `proxyCount` | int | number of stored proxies |
| `updatedAt` / `updatedBy` | datetime / str | last credentials write |

Losing/rotating `CREDENTIALS_ENCRYPTION_KEY` makes the ciphertext
undecryptable — `decrypt_for_run` raises, the run endpoint returns 503 with a
re-enter-the-credentials message.

## `data` document shape

One document per successfully crawled page, written by agent runs
(`data_service.build_data_docs` + `insert_many`, called from
`crawler_service.execute_crawl`) and read by the per-agent listing
(`data_service.list_by_agent`) and the orphaned-data listing
(`data_service.list_orphaned`).

| Field | Type | Set how |
|---|---|---|
| `_id` | str | `str(uuid.uuid4())` — string UUID, **never an ObjectId** |
| `agentId` | str \| None | the agent's `_id` at run start (snapshot); nulled by `data_service.detach_from_agent` when the agent is deleted (or mid-crawl via `crawler_service`, or by the `detach_dangling_agents` startup sweep) |
| `agentName` | str | denormalized for display; keeps the name the crawl ran under even after renames — deliberately survives agent deletion |
| `url` | str | `response.url` — the **post-redirect** final URL, which may differ from the script link |
| `fields` | obj | mirrors the script's keys (minus `links`) in script order; each value is the first matching XPath's text (element matches yield their XPath string-value) or `null` when no XPath matched — `null`, never missing. Facebook agents instead carry the built-in/overridden field set (author, text, timestamp, reactions, comments, mediaUrls, permalink) |
| `crawledAt` | datetime | tz-aware UTC; the same `now` for all docs of one run |

Links that fail to download (non-2xx, timeout, DNS) or that get classified as
a soft failure (login wall, checkpoint, nothing-matched) produce no document —
each lands in the run's `lastRun.failures` with its reason instead;
`count`/`successCount` can be less than `totalLinks`.

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
| `agents` | `uq_name` | `{name: ASCENDING}` | unique | duplicate-name 409s (race-proof, also catches renames on update); listing sorts in-memory until the collection grows |
| `data` | `idx_agent_crawled` | `{agentId: ASCENDING, crawledAt: DESCENDING}` | — | newest-first data per agent (`GET /agents/{agentId}/data`); the `{agentId}` prefix also serves plain equality lookups and the `{"agentId": null}` equality of the orphaned listing |

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

`agents`:

- `insert_one(doc)` — create; `_validate_script` runs **before** any write;
  `DuplicateKeyError` → 409 "Agent name already taken".
- `find_one({"_id": agent_id})` — get.
- `find({}).sort([("createdAt", DESCENDING), ("_id", DESCENDING)]).skip(skip).limit(limit)`
  + `count_documents({})` — listing: same shape and in-memory-sort policy as `list_users`.
- PATCH: `find_one` → `_validate_script` on the merged (old ∪ new) `format`/`script`
  pair → `find_one_and_update({"_id"}, {"$set": {..., "updatedAt", "updatedBy"}}, ReturnDocument.AFTER)`
  with a second `DuplicateKeyError` catch — renaming to a taken name conflicts
  **here**, not at insert. The read-merge-validate-write sequence is not atomic
  across concurrent PATCHes (last-write-wins, same tolerance as the rest of the codebase).
- `delete_one({"_id": agent_id})` — delete, followed by
  `data_service.detach_from_agent(db, agent_id)` (nulls the agent's data docs'
  `agentId`). Delete first, then detach: docs inserted by a crawl finishing
  concurrently land before the detach and are caught by it; anything slipping
  past is healed by the `detach_dangling_agents` startup sweep.
- `find_one_and_update({"_id": agent_id, "status": {"$ne": "Running"}}, {$set: {status: "Running", ...}}, ReturnDocument.AFTER)` —
  the **atomic run claim** in `crawler_service.start_agent_crawl` (refresh-rotation
  idiom): concurrent run requests have exactly one winner; the loser re-reads to
  distinguish 404 (deleted) from 409 (already running). Status flips to
  `Completed`/`Failed` at crawl end target `{_id}` only and tolerate `None`
  (agent deleted mid-crawl).

`data`:

- `insert_many(docs)` — one bulk insert per finished crawl, skipped when the
  crawl yielded 0 items (Motor rejects `insert_many([])`).
- `find({"agentId": id}).sort([("crawledAt", DESCENDING), ("_id", DESCENDING)])
  .skip(skip).limit(limit)` + `count_documents({"agentId": id})` — per-agent
  listing (`list_by_agent`, `GET /agents/{agentId}/data`): newest first, `_id`
  tiebreaker keeps pagination deterministic (one run stamps all its docs with
  the same `crawledAt`); served by `idx_agent_crawled`.
- `update_many({"agentId": id}, {"$set": {"agentId": None}})` — `detach_from_agent`:
  soft-orphaning on agent deletion (`agent_service.delete_agent`) and on mid-crawl
  deletion (`crawler_service.execute_crawl` when the final status flip finds no
  agent). `agentName` deliberately survives for the orphaned listing's display.
- `find({"agentId": None})` (same sort/two-query shape as `list_by_agent`) —
  orphaned-data listing (`list_orphaned`, `GET /data/orphaned`): orphans are
  exactly the docs with `agentId` null (null equality also matches a missing
  field, which never occurs — every doc is inserted with one). Index-served by
  `idx_agent_crawled`, unlike the `$nin` anti-join this replaced. Recreating an
  agent with the same name never relinks orphans: the new agent gets a fresh
  `_id` and `null` is never re-populated.
- `update_many({"agentId": {"$nin": agent_ids, "$ne": None}}, {"$set": {"agentId": None}})`
  — `detach_dangling_agents` startup sweep (lifespan, next to
  `reset_interrupted_crawls`): heals pre-cascade leftovers and the crash window
  between a crawl's insert and its mid-crawl detach. Idempotent; `$ne: None`
  keeps already-orphaned docs out of the write set. `$nin` cannot use
  `idx_agent_crawled` — accepted as a startup-only scan.
- `delete_one({"_id": data_id})` / `delete_many({"_id": {"$in": ids}})` — data
  deletes (`delete_one` / `delete_many`, `DELETE /data/{dataId}` and
  `DELETE /data`); bulk returns `deletedCount`, so unknown ids simply don't count.

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
schema-management mechanism; documents are created inline in the services. Startup
also runs two data-repair sweeps in the lifespan: `reset_interrupted_crawls`
(flips restart-orphaned `Running` agents to `Failed`) and
`detach_dangling_agents` (nulls data-doc `agentId`s matching no live agent).
Inspect data with:

```bash
docker compose exec mongo mongosh -u crawler -p crawlerpass --authenticationDatabase admin data_crawler
```
