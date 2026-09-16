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
- Password constraints live in `PASSWORD_FIELD` and `_check_password_bytes`
  (`app/schemas/auth.py`) — reuse them; never redefine per-endpoint rules.

## Status-code semantics (keep consistent)

- `401`: missing/invalid/expired credential — include `WWW-Authenticate: Bearer` header
- `403`: authenticated but not allowed (wrong current password, banned account, non-admin)
- `409`: unique-constraint conflict (`DuplicateKeyError` → distinct email vs username message)
- `422`: request validation (let Pydantic produce these; don't hand-roll)
- `400`: semantically valid but rejected request (e.g. new password equals current)
