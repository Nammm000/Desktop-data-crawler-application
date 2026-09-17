# Data Crawler Backend

FastAPI + MongoDB backend providing user management and JWT authentication with
rotating refresh tokens.

## Tech Stack

- **FastAPI** — async web framework (interactive docs at `/docs`)
- **MongoDB 8.0** — run via Docker, accessed with **Motor** (async driver)
- **JWT** (PyJWT) — short-lived access tokens + rotating refresh tokens
- **bcrypt** — password hashing

## Getting Started

```bash
# 1. Start MongoDB (Docker)
docker compose up -d

# 2. Create a virtualenv and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env and set JWT_SECRET, e.g.:
#   python3 -c "import secrets; print(secrets.token_urlsafe(48))"

# 4. Run the API
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

`UserOut`: `{id, username, email, role, status, createdAt}` — `passwordHash` is
never exposed. `role` is `admin` or `user`; `status` is a plain string
(`active` / `inactive` / `banned`) and only `active` users may log in.

Admin endpoints: `GET /users` lists newest-first with `?limit=&skip=`; banning or
deactivating a user (`status` ≠ `active`) immediately revokes all of their refresh
tokens (un-banning restores nothing — they log in again); role changes apply on
the target's next request (role is re-read from the DB, not the JWT). Admins get
`400` when targeting their own account, preventing self-lockout.

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
```

## Project Structure

```
app/
├── main.py                  # App factory: lifespan (Mongo connect + indexes), CORS, routers
├── core/config.py           # Settings from .env (pydantic-settings)
├── core/security.py         # bcrypt hashing, JWT create/decode, refresh token primitives
├── db/mongo.py              # Motor client lifecycle, index bootstrap, get_db dependency
├── models/user.py           # UserRole enum, UserStatus constants, collection names
├── schemas/                 # Pydantic request/response models (camelCase aliases)
├── services/user_service.py # User CRUD + duplicate detection
├── services/token_service.py# Refresh token issue/rotate/revoke + reuse detection
└── api/
    ├── deps.py              # get_current_user / get_current_admin dependencies
    └── routes/              # auth.py (5 endpoints), users.py (GET /users/me + 5 admin endpoints)
```

## MongoDB

```bash
# Inspect data
docker compose exec mongo mongosh -u crawler -p crawlerpass \
  --authenticationDatabase admin data_crawler

show collections
db.users.findOne()
db.refresh_tokens.getIndexes()
```

Collections:
- `users` — `{_id: <uuid>, username, email (lowercase), passwordHash, role, status, createdAt, updatedAt}`
  with unique indexes on `email` and `username`.
- `refresh_tokens` — `{_id, tokenHash (sha256), userId, createdAt, expiresAt, revokedAt, replacedBy}`
  with a unique index on `tokenHash`, a `userId` index for revocations, and a TTL
  index that deletes documents once `expiresAt` passes.

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
- Tests (pytest + httpx against a test Mongo)
- First-registered-user-becomes-admin bootstrap (or an admin CLI command)
- Last-admin protection (self-guard exists, but two admins can still demote each other)
- Email verification / password reset flows
