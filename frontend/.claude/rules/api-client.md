---
description: ApiClient endpoint inventory, camelCase JSON contract, and ApiError normalization for app/api/.
globs: ["app/api/**"]
---

# API Client Rules

## Endpoint inventory

| Method | Path | Auth | Body | Returns | Errors |
|---|---|---|---|---|---|
| `health()` | `GET /api/health` | — | — | `{status, database}` | transport |
| `signup()` | `POST /api/v1/auth/signup` | — | `{username, email, password}` | `201 User` | `409` dup, `422` |
| `login()` | `POST /api/v1/auth/login` | — | `{email, password}` | `TokenPair` | `401` generic, `403` not active |
| `refresh()` | `POST /api/v1/auth/refresh` | — | `{refreshToken}` | fresh `TokenPair` (both tokens rotate) | `401` reuse/expired |
| `logout()` | `POST /api/v1/auth/logout` | — | `{refreshToken}` | `None` (204, empty body) | `422` |
| `change_password()` | `POST /api/v1/auth/change-password` | Bearer | `{currentPassword, newPassword}` | fresh `TokenPair` | `403` wrong password, `400` unchanged |
| `get_current_user()` | `GET /api/v1/users/me` | Bearer | — | `User` | `401`, `403` |

## Conventions

- This module is the only place that knows the wire format — camelCase keys
  (`accessToken`, `refreshToken`, `createdAt`) never leak past `app/api/client.py`.
- Lowercase and strip `username` / `email` before sending — mirrors backend validators.
- `logout` returns 204 with an empty body — `_request` returns `None`, never parse it.
- Every request goes through the shared `requests.Session` with the standard
  timeout (10 s) so busy states on buttons can never wedge.
- Base URL resolution: constructor arg → `DATA_CRAWLER_API_URL` → `http://localhost:8000`.

## Errors

- Raise `ApiError(message, status_code)` for every failure — callers branch on `status_code`.
- `{"detail": "..."}` string → use it verbatim; `{"detail": [...]}` (422) →
  `"; "`-joined `field: msg` pairs; anything else → `Request failed (HTTP n)`.
- Transport failures (unreachable, timeout) carry `status_code=None` and the
  message `Could not reach the server at <base_url>`.
- A response that fails to parse into `User` / `TokenPair` raises
  `ApiError("Unexpected response from the server")` — never leak a KeyError to callers.
