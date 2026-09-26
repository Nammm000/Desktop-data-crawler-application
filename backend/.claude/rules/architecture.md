---
paths:
  - "app/**"
---

# Architecture (Full Reference)

## System context

```mermaid
flowchart LR
    Client["Client (frontend/ SPA)"] -->|"HTTPS + JSON /api/v1/*"| CORS["CORSMiddleware<br/>allow_credentials=False"]

    subgraph Backend["FastAPI — app/main.py"]
        CORS --> Routes["api/routes/<br/>thin HTTP layer"]
        Deps["api/deps.py<br/>auth dependencies"]
        Services["services/<br/>business logic"]
        Routes --> Services
    end

    Core["core/config.py (settings)<br/>core/security.py (bcrypt, JWT, token utils)"]
    Schemas["schemas/ (camelCase wire models)"]
    Models["models/ (enums, collection names)"]

    Routes -. uses .- Schemas
    Routes -. uses .- Deps
    Deps -. uses .- Core
    Services -. uses .- Core
    Services -. uses .- Models

    Services -->|"Motor (async)"| Mongo[("MongoDB 8.0<br/>db: data_crawler")]
```

## Layering

Request flow: `api/routes` → `services` → MongoDB via Motor. Config and primitives
live in `core/`.

| Layer | Directory | Responsibility |
|---|---|---|
| HTTP | `app/api/routes/` | Parse request, authorize (via `deps`), call a service, map to a response schema. No collection access here. |
| Auth DI | `app/api/deps.py` | `get_current_user` / `get_current_admin`, `DbDep`, `CurrentUser`, `AdminUser` |
| Business logic | `app/services/` | The only layer that touches Mongo. Returns plain dicts (Mongo documents). |
| Data | `app/db/mongo.py` | Motor client lifecycle, `ensure_indexes()`, `get_db` |
| Primitives | `app/core/` | `config.py` (pydantic-settings), `security.py` (bcrypt, JWT, refresh-token utils) |
| Wire contracts | `app/schemas/` | Pydantic models with camelCase aliases (`_CamelModel`, `UserOut`) |
| Constants | `app/models/` | `UserRole`, `UserStatus`, collection-name constants |

Mapping to response models happens only at the route boundary via
`UserOut.from_doc` — this is where `passwordHash` is dropped and `_id` becomes `id`.

## Application lifecycle (`app/main.py`)

- **Lifespan startup** (`init_mongo`): create `AsyncIOMotorClient(settings.mongodb_uri)`
  (never at import time) → `admin.command("ping")` to fail fast on unreachable host or
  bad credentials → stash client/db on `app.state` → `ensure_indexes(db)` →
  `crawler_service.reset_interrupted_crawls` (flips agents orphaned in `Running` by a
  restart to `Failed`) → `data_service.detach_dangling_agents` (nulls data-doc
  `agentId`s matching no live agent).
- **Lifespan shutdown** (`close_mongo`): `client.close()` if present.
- **Middleware**: only `CORSMiddleware` — `allow_origins=settings.cors_origins_list`,
  `allow_credentials=False` (Bearer headers, not cookies), `allow_methods=["*"]`,
  `allow_headers=["*"]`.
- **Routers**: `auth.router` (`/auth`, tag `auth`), `users.router` (`/users`, tag
  `users`), `agents.router` (`/agents`, tag `agents`), `data.router` (`/data`,
  tag `data` — orphaned-data listing + data deletes), and `notifications.router`
  (`/notifications`, tag `notifications` — one WebSocket endpoint), all mounted
  with `prefix="/api/v1"`.
- **Health**: `GET /api/health` (unversioned) pings Mongo and reports
  `{"status": "ok", "database": "up" | "down"}`.

## Directory map

```
app/
├── main.py                   # FastAPI app: lifespan (Mongo connect + indexes), CORS, routers
├── core/config.py            # Settings from .env; @lru_cache get_settings(); SettingsDep
├── core/security.py          # hash_password, verify_password, dummy_password_check,
│                             # create_access_token, decode_access_token,
│                             # generate_refresh_token, hash_refresh_token
├── db/mongo.py               # init_mongo / close_mongo, ensure_indexes, get_db
├── models/user.py            # UserRole, UserStatus, USERS_COLLECTION, REFRESH_TOKENS_COLLECTION
├── models/agent.py           # AgentType, AgentFormat, AgentStatus, AGENTS_COLLECTION
├── models/data.py            # DATA_COLLECTION
├── schemas/auth.py           # SignupRequest, LoginRequest, RefreshTokenRequest,
│                             # LogoutRequest, ChangePasswordRequest, TokenPair
├── schemas/user.py           # UserOut (+ from_doc boundary)
├── schemas/agent.py          # AgentOut/AgentCreate/AgentUpdate/AgentList
├── schemas/data.py           # DataOut/DataList + delete request/result (+ from_doc boundary)
├── services/user_service.py  # create_user, get_by_email, get_by_id, update_password
├── services/token_service.py # issue/rotate/revoke refresh tokens, reuse detection
├── services/agent_service.py # agent CRUD, _validate_script (json format check);
│                             # delete cascades via data_service.detach_from_agent
├── services/crawler_service.py    # AgentScriptSpider, start_agent_crawl/execute_crawl,
│                             # sse_events (SSE run stream), run-script validation,
│                             # startup Running sweep
├── services/source_pages_discovery.py # source_pages agents: script parsing +
│                             # Playwright listing-page link discovery (next_page /
│                             # load_more button clicking, randomized delays)
├── services/ecommerce_spider.py  # ecommerce agents: two-phase product spider
│                             # (listing -> product pages, pagination following),
│                             # EcommercePlan parser, price/rating transforms
├── services/data_service.py  # build_data_doc + insert_one (per-doc crawl saves), list_by_agent,
│                             # list_orphaned (deleted-agent data), delete_one / delete_many,
│                             # detach_from_agent (agent-delete cascade) + detach_dangling_agents (startup sweep)
├── services/connection_manager.py # shared WS registry (manager.broadcast)
└── api/
    ├── deps.py               # bearer_scheme, DbDep, WsDbDep, CurrentUser, AdminUser (admin guard)
    └── routes/               # auth.py (5 endpoints), users.py (GET /users/me + 5 admin endpoints),
│                             # agents.py (7 agent endpoints, CurrentUser), data.py (3 data endpoints
│                             # incl. GET /orphaned), notifications.py (1 WS endpoint)
```

## Token architecture

| | Access token | Refresh token |
|---|---|---|
| Format | JWT, HS256 | Opaque 384-bit (`secrets.token_urlsafe(48)`) |
| Lifetime | 15 min (`ACCESS_TOKEN_EXPIRE_MINUTES`) | 7 days (`REFRESH_TOKEN_EXPIRE_DAYS`) |
| Transport | `Authorization: Bearer <token>` | JSON body (`refreshToken`) |
| Storage | Stateless (not stored server-side) | MongoDB, **SHA-256 hash only** (`tokenHash`) |
| Claims | `sub` (user id), `role`, `type: "access"`, `iat`, `exp` | n/a |

Key behaviors:

- The JWT is decoded statelessly, but `get_current_user` **re-fetches the user from the
  DB on every request** — bans/status changes apply immediately, and `role` comes from
  the DB doc, not the JWT claim.
- The raw refresh token is shown to the client exactly once; only its hash is stored.
- Refresh rotation is atomic: `find_one_and_update` with
  `{tokenHash, revokedAt: None, expiresAt: {$gt: now}}` — concurrent refreshes with the
  same token have exactly one winner.
- Replaying a revoked token revokes **all** of that user's refresh tokens (family
  revocation). Unknown tokens do **not** cascade (anti-DoS).

## Sequence: login

```mermaid
sequenceDiagram
    participant C as Client
    participant R as POST /auth/login
    participant U as user_service
    participant T as token_service
    participant DB as MongoDB

    C->>R: {email, password}
    R->>U: get_by_email(email)
    U->>DB: find_one({email})
    alt user not found
        R->>R: dummy_password_check(password)
        R-->>C: 401 "Invalid email or password"
    else wrong password
        R->>R: verify_password → false
        R-->>C: 401 "Invalid email or password"
    else status != active
        R-->>C: 403 "Account is not active"
    else success
        R->>T: issue_refresh_token(userId)
        T->>DB: insert_one({tokenHash, userId, expiresAt: +7d})
        T-->>R: raw refresh token
        R->>R: create_access_token(sub=userId, role)
        R-->>C: 200 TokenPair
    end
```

## Sequence: refresh (rotation + reuse detection)

```mermaid
sequenceDiagram
    participant C as Client
    participant R as POST /auth/refresh
    participant T as token_service
    participant DB as MongoDB

    C->>R: {refreshToken}
    R->>T: rotate_refresh_token(raw)
    T->>DB: find_one_and_update({tokenHash, revokedAt: null, expiresAt > now}, {$set: {revokedAt: now, replacedBy: newHash}})
    alt claim won (happy path)
        T->>DB: insert_one(successor token doc)
        T-->>R: (newRaw, userId)
        Note over R: user re-fetched so role/status apply<br/>missing user or inactive status → revoke all, then 401/403
        R-->>C: 200 TokenPair (both tokens rotated)
    else claim lost, doc has revokedAt set (replay)
        T->>DB: update_many({userId, revokedAt: null}, {$set: {revokedAt: now}})
        R-->>C: 401 reuse detected — all sessions revoked
    else expired but not yet TTL-swept
        R-->>C: 401 "Invalid or expired refresh token"
    else unknown token
        Note over T: no revocation cascade (anti-DoS)
        R-->>C: 401 "Invalid or expired refresh token"
    end
```

## Sequence: change password

```mermaid
sequenceDiagram
    participant C as Client
    participant D as deps.get_current_user
    participant R as POST /auth/change-password
    participant U as user_service
    participant T as token_service
    participant DB as MongoDB

    C->>D: Authorization: Bearer access-token
    D->>DB: decode JWT, find_one({_id: sub})
    D-->>R: user doc (status == active, else 401/403)
    C->>R: {currentPassword, newPassword}
    alt wrong current password
        R-->>C: 403 "Current password is incorrect"
    else newPassword == currentPassword
        R-->>C: 400 "New password must be different from the current password"
    else success
        R->>U: update_password(userId, hash(newPassword))
        U->>DB: update_one($set: {passwordHash, updatedAt})
        R->>T: revoke_all_for_user(userId)
        T->>DB: update_many(revoke all non-revoked)
        R->>T: issue_refresh_token(userId)
        T->>DB: insert_one(new token doc)
        R-->>C: 200 TokenPair (single fresh pair)
    end
```

## Sequence: admin bans a user

```mermaid
sequenceDiagram
    participant A as Admin client
    participant R as PATCH /users/{userId}/status
    participant S as user_service
    participant T as token_service
    participant DB as MongoDB

    A->>R: Bearer admin-token + {status: "banned"}
    R->>R: self-target check → 400 if own id
    R->>S: update_user_status(userId, "banned")
    S->>DB: find_one_and_update({_id}, {$set: {status, updatedAt}}, return_document=AFTER)
    alt user not found
        R-->>A: 404 "User not found"
    else updated, status != active
        R->>T: revoke_all_for_user(userId)
        T->>DB: update_many({userId, revokedAt: null}, {$set: {revokedAt: now}})
        R-->>A: 200 UserOut (status: banned)
        Note over DB: target's next request re-fetches the doc → 403<br/>target's refresh tokens are already revoked → 401
    else updated, status == active
        R-->>A: 200 UserOut (no revocation — sessions keep working)
    end
```

Role changes (`PATCH /users/{userId}/role`) follow the same shape minus the
revocation step — `get_current_user` re-reads the DB doc, so a demotion costs the
target their privileges on their very next request despite the stale JWT `role`
claim.

## Sequence: run agent crawl (SSE)

```mermaid
sequenceDiagram
    participant C as Client
    participant R as GET /agents/{id}/run
    participant S as crawler_service
    participant WS as connection_manager
    participant SC as Scrapy (AsyncCrawlerRunner)
    participant DB as MongoDB

    C->>R: Bearer token
    R->>S: start_agent_crawl(db, agentId, email, listener=SSE queue)
    S->>S: _parse_run_script (400 unless json + links + xpath fields)
    S->>DB: find_one_and_update({status != Running} → Running)
    alt claim lost
        R-->>C: 409 (already running) / 404 (deleted) — JSON, stream never opens
    else claimed
        S->>WS: broadcast agentStatus Running
        S->>S: asyncio.create_task(execute_crawl)
        R-->>C: 200 text/event-stream (sse_events: `start`, then queue-driven)
        Note over SC: in-process, pure asyncio<br/>(TWISTED_REACTOR_ENABLED=False)
        SC->>SC: GET each link; per field try XPaths in order
        SC-->>S: on_result({url, fields}) per page (shared results list too)
        S->>DB: data.insert_one (per doc, via the saver task)
        S->>C: SSE event: document (DataOut) — only after the save
        Note over C,S: keep-alive comment every 15 s of silence;<br/>a client disconnect unregisters the queue,<br/>the crawl keeps running
        S->>S: drain saver → lastRun → status → Completed/Stopped/Failed
        S->>WS: broadcast agentStatus Completed + runtimeSeconds + count + data
        S->>C: SSE event: done (closes the stream)
    end
```

In-flight tasks live in `crawler_service._running_crawls` (strong refs +
second re-run guard); the task's `finally` always deregisters. A server restart
kills the crawl — the lifespan startup sweep flips orphaned `Running` agents to
`Failed` so they can be re-run. Deleting the agent mid-crawl is tolerated: the
finishing crawl nulls its just-inserted docs' `agentId` (they land as orphans)
and the status flip no-ops; the `detach_dangling_agents` startup sweep is the
backstop for the crash window.

source_pages runs insert a discovery phase before the crawl: `execute_crawl`
first awaits `source_pages_discovery.discover_links` (plain-asyncio Playwright
headless chromium — NOT scrapy-playwright, whose Twisted handlers never engage
in pure-asyncio mode), which walks the source pages, clicks next_page /
load_more buttons with randomized 3–120 s delays on listing navigations,
dedupes article links, and honors both the stop flag (checked between
navigations and every second of each delay) and the overall
`SOURCE_PAGES_TIMEOUT_SECONDS` deadline. The discovered links then flow
through the regular `AgentScriptSpider` crawl with the remaining budget as
`CLOSESPIDER_TIMEOUT`; `lastRun.totalLinks` counts discovered unique links,
and discovery-phase failures share the same `failures` list/reasons.

ecommerce runs swap the spider class instead: `execute_crawl` runs
`EcommerceProductSpider` (`app/services/ecommerce_spider.py`) with the plan
from `_parse_ecommerce_run_script` (seeds = the script's `links`, pagination
and product caps, per-field overrides) and a politeness settings overlay.
The spider discovers product links itself (`parse` on listing pages —
normalize/dedupe/seed-host-allowlist, then follow `next_page`; a missing
next link is the normal end) and extracts the built-in product fields on
each detail page (`parse_product`; price/rating transforms, absolute
imageUrl, `productUrl` = final URL). Reaching `max_products` stops discovery
and DRAINS the queued requests (exact cap; one `cancelled` entry), a
stop/budget truncation records `discovered - attempted` unfetched products,
and `lastRun.totalLinks` counts discovered unique products via the shared
`stats` dict. Full walkthrough: `ECOMMERCE_CRAWLER_STRATEGY.md`.

## Dependency injection (`app/api/deps.py`, `app/core/config.py`)

- `DbDep` — `Annotated[AsyncIOMotorDatabase, Depends(get_db)]`; `get_db` returns
  `request.app.state.mongo_db`. `WsDbDep` is the WebSocket twin (`get_db_ws` reads
  `websocket.app.state.mongo_db` — `Request` is not injectable in WS routes).
- `CurrentUser` — `Annotated[dict, Depends(get_current_user)]`; returns the fresh user
  doc for any authenticated-user endpoint.
- `AdminUser` — `Annotated[dict, Depends(get_current_admin)]`; 403 "Admin
  privileges required" unless the DB role is `admin`. Used by the five admin
  routes: `GET /users`, `PATCH /users/{userId}/status`, `PATCH /users/{userId}/role`,
  `DELETE /users/{userId}`, `DELETE /users` (bulk).
- `SettingsDep` — `@lru_cache`d `get_settings()`; never `os.getenv`, never
  instantiate `Settings()` elsewhere.
- `bearer_scheme = HTTPBearer(auto_error=False)` — a missing header becomes the
  custom 401 "Not authenticated", not FastAPI's default 403.

## Error & auth conventions

- All 401s from deps carry `WWW-Authenticate: Bearer`.
- Login returns an identical generic `401 "Invalid email or password"` for unknown
  email and wrong password; unknown email runs `dummy_password_check()` (timing
  equalization — no account enumeration).
- Specific service exceptions (`RefreshTokenReuseError`, `RefreshTokenError`) are
  translated to HTTP responses in the route, not in the service.
- Password checks order on login: existence → password → status (a banned user with a
  wrong password gets 401, not 403).

Security invariants live in `security-auth.md`; data-layer details in
`database-schema.md`; the endpoint/status-code table in `api-surface.md` and
`project-overview.md`.
