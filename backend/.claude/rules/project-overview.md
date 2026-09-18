---
paths:
  - "README.md"
  - "docker-compose.yml"
  - ".env.example"
---

# Project Overview (Full Reference)

## What this is

FastAPI + MongoDB backend providing **user management and JWT authentication**:
short-lived access tokens plus rotating refresh tokens with reuse detection.
It is the `backend/` half of the `data-crawler` project; a sibling `frontend/`
SPA consumes this API. App metadata: `FastAPI(title="Data Crawler API", version="0.1.0")`.

## Tech stack

| Component | Version (pinned in `requirements.txt`) |
|---|---|
| Python | 3.14 |
| FastAPI | 0.141.1 |
| Uvicorn | 0.53.0 |
| Motor (async MongoDB driver) | 3.7.1 |
| PyMongo | 4.18.1 |
| Pydantic | 2.13.5 |
| pydantic-settings | 2.15.0 |
| email-validator | 2.3.0 |
| PyJWT | 2.14.0 |
| bcrypt (direct, never passlib) | 5.0.0 |
| websockets (uvicorn WS protocol) | 17.1 |
| MongoDB | 8.0 via Docker (`mongo:8.0`) |

## Features

- Signup with duplicate email/username detection (409, race-proof via unique indexes)
- Login returning an access/refresh token pair (anti-enumeration: generic 401 + timing equalization)
- Token refresh with **rotation** (both tokens rotate) and **reuse detection**
  (replaying a revoked token revokes all of that user's sessions)
- Logout (idempotent single-token revocation)
- Change password (revokes every session, returns one fresh pair)
- `GET /users/me` profile endpoint
- Admin user management: paginated user listing, status changes (bans apply
  immediately), role changes, account deletion (single + bulk, cascades refresh tokens)
- Agent CRUD (any authenticated user): crawler definitions with a raw json/xml/md
  script, unique names, JSON scripts validated on create and on the merged update view
- Agent runs (`GET /agents/{agentId}/run`): executes the script as a background
  Scrapy crawl (in-process, pure asyncio), atomically flips status
  New → Running → Completed/Failed, persists one `data` doc per crawled page,
  and broadcasts status frames (Completed carries the crawled data) to all
  connected WebSocket clients
- Notification WebSocket (any active user): auth-at-handshake stream that
  pushes a placeholder message ("15 minutes have passed") on a timer and
  relays backend broadcasts (agent crawl status) via the shared connection
  manager
- Health check with database status

## API surface

All routes are prefixed `/api/v1`; JSON is camelCase. Errors use FastAPI's
`{"detail": "..."}` shape. `GET /api/health` is the one unversioned route.

| Method | Path | Auth | Body | Success | Errors |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/signup` | — | `{username, email, password}` | `201 UserOut` | `409` "Email already registered" / "Username already taken", `422` |
| POST | `/api/v1/auth/login` | — | `{email, password}` | `200 TokenPair` | `401` generic "Invalid email or password", `403` "Account is not active" |
| POST | `/api/v1/auth/refresh` | — | `{refreshToken}` | `200 TokenPair` (both tokens rotate) | `401` reuse / invalid-or-expired / user-not-found, `403` not active |
| POST | `/api/v1/auth/logout` | — | `{refreshToken}` | `204` (idempotent — unknown token still 204) | `422` |
| POST | `/api/v1/auth/change-password` | Bearer | `{currentPassword, newPassword}` | `200 TokenPair` (fresh pair; all old sessions revoked) | `403` wrong current password, `400` same password, `401`, `422` |
| GET | `/api/v1/users/me` | Bearer | — | `200 UserOut` | `401`, `403` not active |
| GET | `/api/v1/users` | Bearer (admin) | query: `limit` (1–100, def 50), `skip` (≥0, def 0) | `200 UserList` `{users, total}` | `401`, `403` non-admin |
| PATCH | `/api/v1/users/{userId}/status` | Bearer (admin) | `{status}` active/inactive/banned | `200 UserOut` | `401`, `403`, `400` self-target, `404`, `422` |
| PATCH | `/api/v1/users/{userId}/role` | Bearer (admin) | `{role}` admin/user | `200 UserOut` | `401`, `403`, `400` self-target, `404`, `422` |
| DELETE | `/api/v1/users/{userId}` | Bearer (admin) | — | `204` | `401`, `403`, `400` self-target, `404` |
| DELETE | `/api/v1/users` | Bearer (admin) | `{userIds}` (list, ≥1) | `200 {deleted: n}` | `401`, `403`, `400` self in list, `422` |
| POST | `/api/v1/agents` | Bearer | `{name, format, script, type?, status?}` | `201 AgentOut` | `400` invalid JSON script, `409` "Agent name already taken", `422` |
| GET | `/api/v1/agents` | Bearer | query: `limit` (1–100, def 50), `skip` (≥0, def 0) | `200 AgentList` `{agents, total}` | `401` |
| GET | `/api/v1/agents/{agentId}` | Bearer | — | `200 AgentOut` | `401`, `404` "Agent not found" |
| PATCH | `/api/v1/agents/{agentId}` | Bearer | any of `{name, script, format, type, status}` | `200 AgentOut` | `400` invalid JSON script (merged view), `409` dup name, `404`, `422` |
| DELETE | `/api/v1/agents/{agentId}` | Bearer | — | `204` | `401`, `404` |
| GET | `/api/v1/agents/{agentId}/run` | Bearer | — | `202 AgentOut` (status `Running`; crawl runs in the background) | `401`, `404`, `409` "Agent is already running", `400` script not runnable (non-json format, bad structure, too many links) |
| WS | `/api/v1/notifications/ws` | query: `token` (access JWT) | — | ack `{"type":"connected"}`, then `{"type":"notification","message","createdAt"}` per interval plus `{"type":"agentStatus","agentId","agentName","status","createdAt"[,"count","data"|"message"]}` frames on agent runs (broadcast to all clients) | handshake rejection (close 1008 → HTTP 403) |
| GET | `/api/health` | — | — | `200 {"status":"ok","database":"up"\|"down"}` | — |

### Response shapes

`UserOut` (the only user→response boundary; `passwordHash` never appears):

```json
{
  "id": "<uuid string>",
  "username": "smoketest",
  "email": "smoketest@example.com",
  "role": "admin",
  "status": "active",
  "createdAt": "2026-01-01T00:00:00Z"
}
```

`TokenPair` (`expiresIn` is the access-token lifetime in seconds, 900 by default):

```json
{
  "accessToken": "<JWT>",
  "refreshToken": "<opaque url-safe string>",
  "tokenType": "bearer",
  "expiresIn": 900,
  "user": { "...": "UserOut" }
}
```

`UserList` (admin listing, one page newest-first):

```json
{
  "users": [ { "...": "UserOut" } ],
  "total": 42
}
```

`AgentOut` (crawler definition; `script` is the raw file content):

```json
{
  "id": "<uuid string>",
  "name": "Post Fetcher v2",
  "type": "one_post",
  "status": "New",
  "format": "json",
  "script": "{\"url\": \"https://example.com\"}",
  "createdAt": "2026-01-01T00:00:00Z",
  "updatedAt": "2026-01-01T00:00:00Z",
  "updatedBy": "agenta@example.com"
}
```

`AgentList` mirrors `UserList`: `{agents: [AgentOut], total: n}`.

### Request validation constraints (`app/schemas/auth.py`)

| Field | Constraints |
|---|---|
| `username` | 3–32 chars, `^[a-zA-Z0-9_.-]+$`, lowercased before validation |
| `email` | valid `EmailStr`, lowercased |
| `password` (signup / new password) | 8–64 chars **and** ≤ 72 UTF-8 bytes (bcrypt 5 limit) |
| `refreshToken` (refresh / logout) | min 20 chars |
| `currentPassword` (change-password) | unconstrained string |
| `status` (patch status) | `active`, `inactive`, or `banned` |
| `role` (patch role) | `admin` or `user` |
| `userIds` (bulk delete) | list of ids, at least 1 |

Agent constraints (`app/schemas/agent.py`): `name` 1–100 chars, whitespace-stripped,
unique (`409`); `type` `one_post` only (default); `status` one of
`New` (default) / `Running` / `Completed` / `Failed` (the last three are driven by
the run endpoint; PATCHing status **to** `Running` without a crawl soft-locks runs
with `409` until patched back);
`format` `json`/`xml`/`md`; `script` 1–1M chars and must parse via `json.loads` when
`format` is `json` (`400`, validated in `agent_service._validate_script` — also on
the merged PATCH view). `updatedBy` is server-derived from the authenticated user
(creator on create, patcher on update) and never accepted from the request body.
Query params for `GET /agents` mirror `GET /users`.

## Configuration (`.env` via pydantic-settings)

| Env var | Default | Purpose |
|---|---|---|
| `MONGODB_URI` | *(required)* | Motor connection string; needs `authSource=admin` |
| `MONGODB_DB` | `data_crawler` | database name |
| `JWT_SECRET` | *(required)* | HS256 signing key — generate with `secrets.token_urlsafe(48)` |
| `JWT_ALGORITHM` | `HS256` | JWT algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `15` | access-token lifetime |
| `REFRESH_TOKEN_EXPIRE_DAYS` | `7` | refresh-token lifetime |
| `BCRYPT_ROUNDS` | `12` | bcrypt cost factor |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | comma-separated allowed origins (frontend dev servers) |
| `NOTIFICATION_INTERVAL_SECONDS` | `900` | notification push interval over the WS stream (tests shorten it) |
| `CRAWL_TIMEOUT_SECONDS` | `600` | hard ceiling per agent crawl run (Scrapy `CLOSESPIDER_TIMEOUT`) |
| `CRAWL_MAX_PAGES` | `200` | page cap per crawl run (Scrapy `CLOSESPIDER_PAGECOUNT`; more links than this is a `400`) |

## Getting started

```bash
docker compose up -d                                  # MongoDB (check: docker compose ps -> healthy)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env                                  # then set JWT_SECRET
.venv/bin/uvicorn app.main:app --reload --port 8000   # docs at /docs
curl -s localhost:8000/api/health                     # {"status":"ok","database":"up"}
```

## Project layout

```
backend/
├── app/
│   ├── main.py                   # FastAPI app: lifespan, CORS, routers, /api/health
│   ├── api/
│   │   ├── deps.py               # get_current_user / get_current_admin, DbDep, CurrentUser
│   │   └── routes/               # auth.py, users.py, agents.py (6 endpoints, CurrentUser), notifications.py (1 WS endpoint)
│   ├── core/                     # config.py (settings), security.py (bcrypt/JWT/token utils)
│   ├── db/mongo.py               # Motor lifecycle, ensure_indexes(), get_db
│   ├── models/user.py            # UserRole, UserStatus, collection-name constants
│   ├── models/agent.py           # AgentType, AgentFormat, AgentStatus, AGENTS_COLLECTION
│   ├── models/data.py            # DATA_COLLECTION
│   ├── schemas/                  # Pydantic request/response models (camelCase aliases)
│   └── services/                 # user_service.py, token_service.py, agent_service.py,
│       │                         # crawler_service.py (Scrapy run orchestration),
│       │                         # data_service.py (crawl results), connection_manager.py (WS registry)
├── .claude/rules/                # convention + reference docs (this file)
├── docker-compose.yml            # MongoDB 8.0 only (no API service)
├── requirements.txt              # pinned deps
└── .env.example
```

## Known limitations / future work

- **Every signup currently receives `role: "admin"`** — hardcoded in
  `user_service.create_user` (`app/services/user_service.py`); no admin bootstrap yet.
- No rate limiting (login/refresh endpoints are unprotected).
- No test suite.
- `GET /agents/{agentId}/run` is a GET with side effects (explicit user choice);
  Bearer auth keeps prefetchers from triggering crawls.
- Crawl results have no read API yet — they reach the frontend via the WS
  Completed frame and live in the `data` collection (`GET /data?agentId=` is
  future work).
- A server restart (including `uvicorn --reload`) kills in-flight crawls; the
  startup sweep flips orphaned `Running` agents to `Failed`. Run without
  `--reload` when testing long crawls.
- No email verification or password reset.
- No last-admin protection: the self-guard only blocks self-targeting — two admins
  can still ban/demote each other. Un-banning does not restore revoked sessions
  (the user must log in again).

## Where to go deeper

- `architecture.md` — layering, request lifecycle, token architecture, sequence diagrams
- `database-schema.md` — collections, document shapes, indexes, TTL semantics
- `security-auth.md` — token model and security invariants
- `api-surface.md` — endpoint and status-code conventions
