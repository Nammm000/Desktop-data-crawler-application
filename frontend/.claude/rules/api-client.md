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
| `list_users()` | `GET /api/v1/users?limit&skip` | Bearer | — | `UserPage(users, total)` | `403` non-admin, `422` bad params |
| `update_user_status()` | `PATCH /api/v1/users/{id}/status` | Bearer | `{status}` (`active/inactive/banned`) | `User` | `400` self-change, `404`, `403` |
| `update_user_role()` | `PATCH /api/v1/users/{id}/role` | Bearer | `{role}` (`admin/user`) | `User` | `400` self-change, `404`, `403` |
| `delete_user()` | `DELETE /api/v1/users/{id}` | Bearer | — | `None` (204, empty body) | `400` self-delete, `404`, `403` |
| `delete_users()` | `DELETE /api/v1/users` | Bearer | `{userIds}` (min 1) | `int` (deleted count) | `400` self-in-list, `422`, `403` |
| `list_agents()` | `GET /api/v1/agents?limit&skip` | Bearer | — | `AgentPage(agents, total)` | `422` bad params |
| `create_agent()` | `POST /api/v1/agents` | Bearer | `{name, format, script}` | `201 Agent` | `409` dup name, `400` invalid JSON script, `422` |
| `update_agent()` | `PATCH /api/v1/agents/{id}` | Bearer | `{name?, format?, script?}` (partial) | `Agent` | `409` dup name, `400` JSON check on merged script, `404`, `422` |
| `delete_agent()` | `DELETE /api/v1/agents/{id}` | Bearer | — | `None` (204, empty body) | `404` |

## Conventions

- This module is the only place that knows the wire format — camelCase keys
  (`accessToken`, `refreshToken`, `createdAt`) never leak past `app/api/client.py`.
- Lowercase and strip `username` / `email` before sending — mirrors backend validators.
- `list_users` is skip/limit style (`limit` 1–100, default 50; `skip` ≥ 0), sorted
  newest-first server-side; the `{"users": [...], "total": n}` envelope parses into
  `UserPage`. The list/status/role endpoints are admin-only (403 otherwise).
- `logout` returns 204 with an empty body — `_request` returns `None`, never parse it.
  `delete_user` behaves the same way (204, never parsed).
- `delete_users` (bulk) is idempotent for unknown ids — the backend returns how
  many it actually deleted; both delete endpoints also revoke the targets'
  refresh tokens server-side.
- Agent endpoints are for **every authenticated active user** (unlike the
  admin-only user list/status/role/delete). The camelCase `AgentOut`
  (`createdAt`, `updatedAt`, `updatedBy`) parses into `Agent`; `type` is
  always `"one_post"` and `status` `"New"` (display-only). There is no
  `get_agent` single fetch — the list plus row data cover the UI.
  `create_agent` / `update_agent` strip `name` before sending (mirrors
  `signup`); `delete_agent` is 204-never-parsed like `delete_user`.
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
