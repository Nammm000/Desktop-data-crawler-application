---
paths:
  - "app/api/**"
  - "app/schemas/**"
---

# API Surface Rules

## Endpoint inventory

| Method | Path | Auth | Body | Success | Errors |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/signup` | — | `{username, email, password}` | `201 UserOut` | `409` dup email/username, `422` |
| POST | `/api/v1/auth/login` | — | `{email, password}` | `200 TokenPair` | `401` generic, `403` not active |
| POST | `/api/v1/auth/refresh` | — | `{refreshToken}` | `200 TokenPair` (both rotated) | `401` invalid/expired/reuse |
| POST | `/api/v1/auth/logout` | — | `{refreshToken}` | `204` idempotent | `422` |
| POST | `/api/v1/auth/change-password` | Bearer | `{currentPassword, newPassword}` | `200 TokenPair` | `401`, `403`, `400` same pw, `422` |
| GET | `/api/v1/users/me` | Bearer | — | `200 UserOut` | `401`, `403` |
| GET | `/api/v1/users` | Bearer (admin) | query: `limit` (1–100, def 50), `skip` (≥0, def 0) | `200 UserList` `{users, total}` | `401`, `403` |
| PATCH | `/api/v1/users/{userId}/status` | Bearer (admin) | `{status}` active/inactive/banned | `200 UserOut` | `401`, `403`, `400` self-target, `404`, `422` |
| PATCH | `/api/v1/users/{userId}/role` | Bearer (admin) | `{role}` admin/user | `200 UserOut` | `401`, `403`, `400` self-target, `404`, `422` |
| DELETE | `/api/v1/users/{userId}` | Bearer (admin) | — | `204` | `401`, `403`, `400` self-target, `404` |
| DELETE | `/api/v1/users` | Bearer (admin) | `{userIds}` (list, ≥1) | `200 {deleted: n}` | `401`, `403`, `400` self in list, `422` |
| POST | `/api/v1/agents` | Bearer | `{name, format, script, type?, status?}` | `201 AgentOut` | `400` invalid JSON script, `409` dup name, `422` |
| GET | `/api/v1/agents` | Bearer | query: `limit` (1–100, def 50), `skip` (≥0, def 0) | `200 AgentList` `{agents, total}` | `401` |
| GET | `/api/v1/agents/{agentId}` | Bearer | — | `200 AgentOut` | `401`, `404` |
| PATCH | `/api/v1/agents/{agentId}` | Bearer | any of `{name, script, format, type, status}` | `200 AgentOut` | `400` invalid JSON script (merged view), `409` dup name, `404`, `422` |
| DELETE | `/api/v1/agents/{agentId}` | Bearer | — | `204` | `401`, `404` |

When adding/removing/changing an endpoint, update this table and the README.

## Conventions

- Routers: `APIRouter(prefix="/...", tags=[...])`, mounted in `app/main.py` under
  `prefix="/api/v1"`. Route functions are `async`.
- Routes stay thin: validate → call a function in `app/services/` → map to a response
  schema. No collection access directly from route files.
- Every route declares `response_model=` pointing at a schema in `app/schemas/`.
- JSON is camelCase: request/response models use
  `ConfigDict(alias_generator=to_camel, populate_by_name=True)` (see `_CamelModel`
  in `app/schemas/auth.py`). Python fields stay snake_case.
- Errors always use FastAPI's `{"detail": "..."}` shape via `HTTPException`.
- Admin endpoints guard via `AdminUser` (`Depends(get_current_admin)`, `app/api/deps.py`).
  Setting a user's status to anything but `active` also revokes all of that user's
  refresh tokens; deleting a user hard-deletes their refresh tokens (a replayed token
  then reads as unknown → 401, no cascade); admins cannot target their own account
  (400 self-guard — status, role, and delete).
- Password constraints live in `PASSWORD_FIELD` and `_check_password_bytes`
  (`app/schemas/auth.py`) — reuse them; never redefine per-endpoint rules.
- Agent endpoints are open to every authenticated user (`CurrentUser`). When the
  effective `format` is `json`, the effective `script` must parse via `json.loads` —
  validated in `agent_service._validate_script` on create and on the merged
  (old ∪ new) PATCH view (`400` otherwise); `xml`/`md` scripts are stored as-is.
  Agent names are unique (`409` on create and on rename). `updatedBy` is
  server-derived from the authenticated user (creator on create, patcher on
  update) — never accepted from the request body.

## Status-code semantics (keep consistent)

- `401`: missing/invalid/expired credential — include `WWW-Authenticate: Bearer` header
- `403`: authenticated but not allowed (wrong current password, banned account, non-admin)
- `409`: unique-constraint conflict (`DuplicateKeyError` → distinct email vs username message)
- `422`: request validation (let Pydantic produce these; don't hand-roll)
- `400`: semantically valid but rejected request (e.g. new password equals current,
  admin targeting their own account)
- `404`: path parameter matches no document (e.g. unknown `{userId}`)
