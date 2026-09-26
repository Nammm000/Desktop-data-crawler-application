# Data Crawler Backend

FastAPI + MongoDB backend providing user management and JWT authentication with
rotating refresh tokens.

## Tech Stack

- **FastAPI** — async web framework (interactive docs at `/docs`)
- **MongoDB 8.0** — run via Docker, accessed with **Motor** (async driver)
- **JWT** (PyJWT) — short-lived access tokens + rotating refresh tokens
- **bcrypt** — password hashing
- **Scrapy** — web crawler driven by agent scripts (runs in-process on the
  asyncio loop, no separate worker)

## Getting Started

```bash
# 1. Start MongoDB (Docker)
docker compose up -d

# 2. Create a virtualenv and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. One-time browser download for source_pages agents (~120 MB;
#    skip if you only use generic/facebook agents)
playwright install chromium

# 4. Configure environment
cp .env.example .env
# Edit .env and set JWT_SECRET, e.g.:
#   python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# 5. Run the API
uvicorn app.main:app --reload --port 8000
```

The API is served at `http://localhost:8000` (Swagger UI at `/docs`, health check
at `/api/health`). Indexes are created automatically on startup.

## Authentication Design

- **Access token** — JWT, 15 min lifetime, sent as `Authorization: Bearer <token>`.
  Verified statelessly, but the user document is re-fetched on every request so
  bans/status changes take effect immediately.
- **Refresh token** — opaque 384-bit random string, 7 day lifetime, stored as a
  SHA-256 hash in MongoDB (with a TTL index for automatic cleanup). Travels in
  the JSON body (not cookies — keeps cross-origin SPA dev simple; rotate it into
  an `HttpOnly` cookie if the app is ever served same-origin).
- **Rotation** — every `/auth/refresh` revokes the presented token and issues a
  new pair. The claim is atomic (`find_one_and_update` on `revokedAt: None`), so
  concurrent refreshes with the same token cannot both succeed.
- **Reuse detection** — replaying an already-revoked token revokes *all* refresh
  tokens of that user (stolen tokens burn the whole session family).
- **Change password** — revokes all sessions and returns one fresh pair.

## API

All routes are prefixed with `/api/v1`. JSON uses camelCase
(`accessToken`, `refreshToken`, `createdAt`, ...).

| Method | Path | Auth | Body | Success | Main errors |
|---|---|---|---|---|---|
| POST | `/auth/signup` | — | `{username, email, password}` | `201` `UserOut` | `409` duplicate email/username, `422` validation |
| POST | `/auth/login` | — | `{email, password}` | `200` `TokenPair` | `401` invalid credentials, `403` not active |
| POST | `/auth/refresh` | — | `{refreshToken}` | `200` new `TokenPair` | `401` invalid/expired/reuse detected |
| POST | `/auth/logout` | — | `{refreshToken}` | `204` (idempotent) | `422` |
| POST | `/auth/change-password` | Bearer | `{currentPassword, newPassword}` | `200` fresh `TokenPair` | `401` bad token, `403` wrong password, `400` same password |
| GET | `/users/me` | Bearer | — | `200` `UserOut` | `401`, `403` not active |
| GET | `/users` | Bearer (admin) | query: `limit` (1–100, def 50), `skip` (≥0) | `200` `UserList` `{users, total}` | `401`, `403` non-admin |
| PATCH | `/users/{userId}/status` | Bearer (admin) | `{status}` active/inactive/banned | `200` `UserOut` | `401`, `403`, `400` self-target, `404`, `422` |
| PATCH | `/users/{userId}/role` | Bearer (admin) | `{role}` admin/user | `200` `UserOut` | `401`, `403`, `400` self-target, `404`, `422` |
| DELETE | `/users/{userId}` | Bearer (admin) | — | `204` | `401`, `403`, `400` self-target, `404` |
| DELETE | `/users` | Bearer (admin) | `{userIds}` (list, ≥1) | `200` `{"deleted": n}` | `401`, `403`, `400` self in list, `422` |
| POST | `/agents` | Bearer | `{name, format, script, type?, status?}` | `201` `AgentOut` | `400` invalid JSON script, `409` dup name, `422` |
| GET | `/agents` | Bearer | query: `limit` (1–100, def 50), `skip` (≥0) | `200` `AgentList` `{agents, total}` | `401` |
| GET | `/agents/{agentId}` | Bearer | — | `200` `AgentOut` | `401`, `404` |
| PATCH | `/agents/{agentId}` | Bearer | any of `{name, script, format, type, status}` | `200` `AgentOut` | `400` invalid JSON script, `409` dup name, `404`, `422` |
| DELETE | `/agents/{agentId}` | Bearer | — | `204` (its data docs' `agentId` → `null`; `agentName` kept) | `401`, `404` |
| GET | `/agents/{agentId}/run` | Bearer | — | `200` SSE stream (`text/event-stream`): `start` → one `document` event per crawled page as it is saved → terminal `done` event; `: keep-alive` comments every 15 s idle; disconnecting the stream does NOT stop the crawl | `401`, `404`, `409` already running, `400` script not runnable (raised as JSON before the stream opens) |
| GET | `/agents/{agentId}/data` | Bearer | query: `limit` (1–100, def 50), `skip` (≥0) | `200` `DataList` `{data, total}` (newest first) | `401`, `404` |
| GET | `/data/orphaned` | Bearer | query: `limit` (1–100, def 50), `skip` (≥0) | `200` `DataList` `{data, total}` (newest first; records whose agent was deleted — `agentId` is `null`) | `401`, `422` |
| DELETE | `/data` | Bearer | `{ids}` (list, ≥1) | `200` `{"deleted": n}` | `401`, `422` |
| DELETE | `/data/{dataId}` | Bearer | — | `204` | `401`, `404` |
| WS | `/notifications/ws?token=<accessToken>` | query token | — | ack `{"type":"connected"}`, then a `{"type":"notification","message","createdAt"}` frame every `NOTIFICATION_INTERVAL_SECONDS` (default 900) plus `{"type":"agentStatus", ...}` frames on agent runs (Completed/Failed carry `runtimeSeconds`) | handshake rejected with close code 1008 (invalid/expired token, inactive user) |

`UserOut`: `{id, username, email, role, status, createdAt}` — `passwordHash` is
never exposed. `role` is `admin` or `user`; `status` is a plain string
(`active` / `inactive` / `banned`) and only `active` users may log in.

Admin endpoints: `GET /users` lists newest-first with `?limit=&skip=`; banning or
deactivating a user (`status` ≠ `active`) immediately revokes all of their refresh
tokens (un-banning restores nothing — they log in again); role changes apply on
the target's next request (role is re-read from the DB, not the JWT). Admins get
`400` when targeting their own account, preventing self-lockout.

`AgentOut`: `{id, name, type, status, format, script, createdAt, updatedAt, updatedBy}` —
agents are crawler definitions available to every authenticated user. `type` defaults
to `one_post`; `status` is `New`, `Running`, `Completed`, or `Failed` (default `New`;
`Running`/`Completed`/`Failed` are managed by the run endpoint); `format` is
`json`, `xml`, or `md` and
describes how `script` (the raw text content of the file) should be parsed. When
`format` is `json`, the script must be valid JSON (`400` otherwise — checked on
create and on update, where old and new values are validated together, so switching
only the format to `json` against a stored non-JSON script is rejected). Names are
unique (1–100 chars, whitespace-stripped). `updatedBy` records the email of the
acting user — the creator on create, the patcher on update — and is set by the
server, never accepted from the request body.

### Source-pages agents (`sourceType: "source_pages"`)

Instead of an explicit `links` list, the agent discovers article links from
listing pages — optionally clicking a "next page" or "load more" button in a
headless Chromium (Playwright) — then crawls the discovered articles with the
remaining field XPaths exactly like a generic agent. Script contract:

```json
{
  "source_pages": ["https://e.vnexpress.net/news/tech/tech-news"],
  "post_link": "//*[contains(@class, 'title')]/a/@href",
  "next_page": "//a[contains(@id, '_load_more')]",
  "max_next": 3,
  "title": ["//meta[@property='og:title']/@content", "//title"],
  "published_time": "//meta[@property='article:published_time']/@content"
}
```

- `source_pages` (required) — listing-page URLs; `post_link` (required) — one
  XPath or a list (results are unioned, deduplicated across the whole run).
- `next_page` OR `load_more` (optional, mutually exclusive — a script with
  both is a `400` on create and on run) — button XPath to click. `max_next`
  (optional, `max_next_page` accepted as an alias) caps navigations/clicks;
  without it, pagination continues while the button exists.
- Every other key is a field name mapped to one XPath or a fallback list,
  extracted from each discovered article.
- Listing-page navigations pause a randomized 3–120 s
  (`SOURCE_PAGES_DELAY_MIN/MAX_SECONDS`) to look human; article pages crawl
  under the gentler `SOURCE_PAGES_ARTICLE_DOWNLOAD_DELAY`. The whole run
  (discovery + crawl) is bounded by `SOURCE_PAGES_TIMEOUT_SECONDS` (default
  1 h) and `CRAWL_MAX_PAGES` unique links. Requires the one-time
  `playwright install chromium`.

`DataOut`: `{id, agentId, agentName, url, fields, crawledAt}` — one crawled page
produced by an agent run (`fields` maps each script field to its extracted text or
`null`; `url` is the post-redirect final URL). `GET /agents/{agentId}/data` lists
an agent's records newest-first with `?limit=&skip=`, wrapped as
`DataList` `{data, total}`; an agent with no runs returns an empty list, an unknown
agent id returns `404`. Deleting an agent nulls `agentId` on its data docs
(`agentName` is kept); they become unreadable via this endpoint (404) and are
listed by `GET /data/orphaned` instead. A startup sweep (`data_service.
detach_dangling_agents`) nulls any remaining `agentId` matching no live agent, so
dangling ids can't hide. A recreated same-name agent never relinks them (fresh
`_id`; `null` is never re-populated). Records are deleted with
`DELETE /data/{dataId}` (single → `204`) or `DELETE /data` + `{ids}` (bulk →
`{"deleted": n}`; unknown ids don't count) — no agent scoping on deletes.

### Quick smoke test

```bash
BASE=http://localhost:8000/api/v1

# Sign up
curl -s -X POST $BASE/auth/signup -H 'Content-Type: application/json' \
  -d '{"username":"alice","email":"alice@example.com","password":"S3curePass!"}'

# Login -> token pair
curl -s -X POST $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"email":"alice@example.com","password":"S3curePass!"}'

# Protected endpoint
curl -s $BASE/users/me -H "Authorization: Bearer <accessToken>"

# Admin: list users (paginated) / change status / change role
curl -s "$BASE/users?limit=20&skip=0" -H "Authorization: Bearer <accessToken>"
curl -s -X PATCH $BASE/users/<userId>/status -H "Authorization: Bearer <accessToken>" \
  -H 'Content-Type: application/json' -d '{"status":"banned"}'
curl -s -X PATCH $BASE/users/<userId>/role -H "Authorization: Bearer <accessToken>" \
  -H 'Content-Type: application/json' -d '{"role":"user"}'

# Admin: delete one user / delete many users (cascades their refresh tokens)
curl -s -X DELETE $BASE/users/<userId> -H "Authorization: Bearer <accessToken>"
curl -s -X DELETE $BASE/users -H "Authorization: Bearer <accessToken>" \
  -H 'Content-Type: application/json' -d '{"userIds":["<userId1>","<userId2>"]}'

# Rotate tokens
curl -s -X POST $BASE/auth/refresh -H 'Content-Type: application/json' \
  -d '{"refreshToken":"<refreshToken>"}'

# Logout
curl -i -X POST $BASE/auth/logout -H 'Content-Type: application/json' \
  -d '{"refreshToken":"<refreshToken>"}'

# Agents: create (md / json script) -> list -> update -> delete
curl -s -X POST $BASE/agents -H "Authorization: Bearer <accessToken>" \
  -H 'Content-Type: application/json' \
  -d '{"name":"my-agent","format":"md","script":"# Steps\n1. fetch post"}'
curl -s -X POST $BASE/agents -H "Authorization: Bearer <accessToken>" \
  -H 'Content-Type: application/json' \
  -d '{"name":"json-agent","format":"json","script":"{\"url\":\"https://example.com\"}"}'
curl -s "$BASE/agents?limit=20&skip=0" -H "Authorization: Bearer <accessToken>"
curl -s -X PATCH $BASE/agents/<agentId> -H "Authorization: Bearer <accessToken>" \
  -H 'Content-Type: application/json' -d '{"name":"renamed-agent"}'
curl -s -X DELETE $BASE/agents/<agentId> -H "Authorization: Bearer <accessToken>"

# Notification stream (WebSocket; temporary 15-min placeholder push).
# Shorten the interval for testing: NOTIFICATION_INTERVAL_SECONDS=3 uvicorn ...
.venv/bin/python -c "
import asyncio, websockets
async def main():
    async with websockets.connect('ws://localhost:8000/api/v1/notifications/ws?token=<accessToken>') as ws:
        print(await ws.recv())  # {\"type\": \"connected\"}
        print(await ws.recv())  # first notification
asyncio.run(main())"

# Run an agent: an SSE stream (-N to disable buffering). Events: `start`,
# then one `document` per crawled page as it is SAVED (DataOut payload), then
# terminal `done` with {status, runtimeSeconds, count, lastRun} — the same
# outcome also goes out as an agentStatus frame on the WS above. Start errors
# (409/400/404/401) come back as ordinary JSON before the stream opens.
# A runnable script is a JSON object: "links" = list of URLs to crawl, every
# other key = field name mapped to one XPath or a list of fallback XPaths
# (tried in order until one matches; element matches yield their text).
curl -sN $BASE/agents/<agentId>/run -H "Authorization: Bearer <accessToken>"

# List the crawled data an agent produced (newest first, paginated)
curl -s "$BASE/agents/<agentId>/data?limit=20&skip=0" -H "Authorization: Bearer <accessToken>"

# Delete an agent: its crawled data survives with agentId nulled (orphaned)
curl -s -X DELETE $BASE/agents/<agentId> -H "Authorization: Bearer <accessToken>"

# List crawled data whose agent has been deleted — agentId is null (newest first)
curl -s "$BASE/data/orphaned?limit=20&skip=0" -H "Authorization: Bearer <accessToken>"

# Delete data documents: one by id, or many by ids (unknown ids don't count)
curl -s -X DELETE $BASE/data/<dataId> -H "Authorization: Bearer <accessToken>"
curl -s -X DELETE $BASE/data -H "Authorization: Bearer <accessToken>" \
  -H 'Content-Type: application/json' -d '{"ids":["<dataId1>","<dataId2>"]}'
```

## Project Structure

```
app/
├── main.py                  # App factory: lifespan (Mongo connect + indexes + startup sweeps), CORS, routers
├── core/config.py           # Settings from .env (pydantic-settings)
├── core/security.py         # bcrypt hashing, JWT create/decode, refresh token primitives
├── db/mongo.py              # Motor client lifecycle, index bootstrap, get_db dependency
├── models/user.py           # UserRole enum, UserStatus constants, collection names
├── models/agent.py          # AgentType/AgentFormat/AgentStatus constants, AGENTS_COLLECTION
├── models/data.py           # DATA_COLLECTION
├── schemas/                 # Pydantic request/response models (camelCase aliases)
├── services/user_service.py # User CRUD + duplicate detection
├── services/token_service.py# Refresh token issue/rotate/revoke + reuse detection
├── services/agent_service.py# Agent CRUD + script JSON validation
├── services/crawler_service.py  # Scrapy spider + run orchestration (status flips, WS broadcast)
├── services/source_pages_discovery.py # source_pages agents: script parsing + Playwright listing-page link discovery (next_page/load_more clicking)
├── services/data_service.py # data collection persistence for crawl results + per-agent/orphaned listings + agent-delete detach
├── services/connection_manager.py # shared WS registry for backend broadcasts
└── api/
    ├── deps.py              # get_current_user / get_current_admin dependencies
    └── routes/              # auth.py, users.py, agents.py (7 agent endpoints), data.py (3 data endpoints incl. GET /orphaned), notifications.py (1 WS endpoint)
```

## MongoDB

```bash
# Inspect data
docker compose exec mongo mongosh -u crawler -p crawlerpass \
  --authenticationDatabase admin data_crawler

show collections
db.users.findOne()
db.refresh_tokens.getIndexes()
db.agents.getIndexes()
```

Collections:
- `users` — `{_id: <uuid>, username, email (lowercase), passwordHash, role, status, createdAt, updatedAt}`
  with unique indexes on `email` and `username`.
- `refresh_tokens` — `{_id, tokenHash (sha256), userId, createdAt, expiresAt, revokedAt, replacedBy}`
  with a unique index on `tokenHash`, a `userId` index for revocations, and a TTL
  index that deletes documents once `expiresAt` passes.
- `agents` — `{_id, name, type, status, format, script, createdAt, updatedAt, updatedBy}`
  with a unique index on `name`.
- `data` — one document per crawled page:
  `{_id, agentId, agentName, url, fields: {field: value | null}, crawledAt}` with a
  compound `{agentId, crawledAt}` index. Written by agent runs and read by
  `GET /agents/{agentId}/data` (and `GET /data/orphaned` for docs whose agent was
  deleted — `agentId` is nulled on deletion, `agentName` kept; a startup sweep
  nulls any dangling ids); unmatched fields are `null`, never missing.

## Notes

- User ids are string UUIDs stored in `_id` (not ObjectId) — clean JWT `sub`
  claims and stable in URLs/logs.
- Passwords are capped at 64 chars / 72 bytes: bcrypt 5 raises for longer input
  and multibyte UTF-8 can exceed 72 bytes before 64 characters.
- Login burns equal CPU time whether or not the email exists (anti
  account-enumeration) and returns a generic error message.
- Motor is in maintenance mode; PyMongo's native `AsyncMongoClient` is its
  eventual successor. The `db/mongo.py` seam isolates the swap to one file.

## Future Work

- Rate limiting on `/auth/login`
- Integration tests (pytest + httpx against a test Mongo) — the pure helpers
  (`tests/test_pure_helpers.py`) already run with `.venv/bin/pytest tests/`
- Last-admin protection (self-guard exists, but two admins can still demote each other)
- Email verification / password reset flows

Done since the last revision: first-signup-becomes-admin bootstrap (later
signups are regular users), crawl stop/cancel (`POST /agents/{id}/stop`),
per-link failure reasons on every run (`lastRun`), Facebook post agents
(`sourceType: "facebook"` — cookies/proxies stored Fernet-encrypted in
`agent_secrets`, never returned by the API), bcrypt moved off the event loop,
an http/https-only scheme allowlist for crawl links, and source-pages agents
(`sourceType: "source_pages"` — Playwright listing-page discovery with
next_page/load_more clicking feeding the generic XPath spider).
