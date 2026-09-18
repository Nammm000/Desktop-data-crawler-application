# Data Crawler Backend

FastAPI + MongoDB backend providing user management and JWT authentication
(access tokens with rotating refresh tokens + reuse detection). Part of the
`data-crawler` project; a sibling `frontend/` SPA will consume this API.

## Environment

- Python 3.14, virtualenv at `.venv/` (always use `.venv/bin/python` / `.venv/bin/pip`)
- MongoDB 8.0 via Docker on `localhost:27017` (user `crawler` / pass `crawlerpass`)
- Dependencies are pinned in `requirements.txt`

## Commands

```bash
docker compose up -d                                  # start MongoDB (check: docker compose ps -> healthy)
.venv/bin/pip install -r requirements.txt             # install deps
.venv/bin/uvicorn app.main:app --reload --port 8000   # run API (docs at /docs)
curl -s localhost:8000/api/health                     # expect {"status":"ok","database":"up"}

# Inspect data (requires the container to be running)
docker compose exec mongo mongosh -u crawler -p crawlerpass --authenticationDatabase admin data_crawler
```

Setup for a fresh clone: create the venv, install deps, then
`cp .env.example .env` and set `JWT_SECRET`
(`python3 -c "import secrets; print(secrets.token_urlsafe(48))"`).

## Architecture

Request flow: `api/routes` (thin HTTP layer) → `services` (business logic) →
MongoDB via Motor. Config and primitives live in `core/`.

```
app/
├── main.py                   # FastAPI app: lifespan (Mongo connect + indexes), CORS, routers
├── core/config.py            # Settings from .env (pydantic-settings)
├── core/security.py          # bcrypt, JWT access tokens, refresh token primitives
├── db/mongo.py               # Motor client lifecycle, ensure_indexes(), get_db
├── models/user.py            # UserRole enum, UserStatus constants, collection names
├── models/agent.py           # AgentType/AgentFormat constants, AGENTS_COLLECTION
├── schemas/                  # Pydantic request/response models (camelCase aliases)
├── services/user_service.py  # user CRUD, duplicate detection, password updates
├── services/token_service.py # refresh token issue/rotate/revoke, reuse detection
├── services/agent_service.py # agent CRUD, script JSON validation
└── api/
    ├── deps.py               # get_current_user / get_current_admin, DbDep, CurrentUser
    └── routes/               # auth.py (5 auth endpoints), users.py (GET /users/me), agents.py (5 agent endpoints), notifications.py (1 WS endpoint)
```

## Golden Rules

- All routes are prefixed `/api/v1`; JSON is camelCase (`accessToken`, `createdAt`)
- `passwordHash` is never exposed in any response — `UserOut.from_doc` is the boundary
- All datetimes are tz-aware UTC (`datetime.now(timezone.utc)`)
- User `_id` is a string UUID, never an ObjectId
- Indexes change only in `ensure_indexes()` in `app/db/mongo.py`
- Verify auth changes with the curl flow in `.claude/rules/verification.md` before finishing

## Rule Files

Topic-specific conventions live in `.claude/rules/`:

| File | Topic | Loads |
|---|---|---|
| `api-surface.md` | Endpoints, status codes, request/response conventions | when editing `app/api/` or `app/schemas/` |
| `security-auth.md` | Token model, auth invariants, security requirements | every session |
| `database-mongodb.md` | Motor usage, collections, index policy | when editing `app/db/`, `app/services/`, `docker-compose.yml` |
| `coding-conventions.md` | Python/Pydantic style, layering, dependency injection | when editing `app/**/*.py` |
| `verification.md` | End-to-end smoke-test workflow | every session |
| `project-overview.md` | Full reference: stack, features, API surface, config, setup | when editing `README.md`, `docker-compose.yml`, `.env.example` |
| `architecture.md` | Layering, request lifecycle, token architecture, sequence diagrams | when editing `app/**` |
| `database-schema.md` | Collections, document shapes, indexes, TTL, query patterns | when editing `app/db/`, `app/services/`, `app/models/`, `docker-compose.yml` |
