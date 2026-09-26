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
| `create_agent()` | `POST /api/v1/agents` | Bearer | `{name, format, script, sourceType?}` | `201 Agent` | `409` dup name, `400` invalid JSON script (or invalid source_pages structure — both pagination buttons, missing `source_pages`/`post_link`, bad `max_next`; or invalid ecommerce structure — bad `links`, both `max_next` spellings, bad `max_next`/`max_products`, non-XPath field), `422` |
| `update_agent()` | `PATCH /api/v1/agents/{id}` | Bearer | `{name?, format?, script?, sourceType?}` (partial) | `Agent` | `409` dup name, `400` JSON check / source_pages / ecommerce structure check on merged view, `404`, `422` |
| `delete_agent()` | `DELETE /api/v1/agents/{id}` | Bearer | — | `None` (204, empty body) | `404` |
| `stream_agent_run()` | `GET /api/v1/agents/{id}/run` (SSE) | Bearer | — | blocking stream of `AgentRunEvent`s: `start` → `document` (AgentData per saved page) → `done` (AgentRunDone) | `404`, `409` already running, `400` script not runnable (incl. non-facebook links on a facebook agent), `503` credentials undecryptable — raised before any event |
| `stop_agent()` | `POST /api/v1/agents/{id}/stop` | Bearer | — | `202 Agent` (still `Running`; the Stopped outcome arrives via WS) | `404`, `409` not running |
| `set_agent_credentials()` | `PUT /api/v1/agents/{id}/credentials` | Bearer | `{cookieHeader?, proxyText?}` (raw pastes) | `200 Agent` (flags updated; values stored encrypted, never returned) | `400` unparseable, `404`, `503` key unset |
| `get_agent_credentials_metadata()` | `GET /api/v1/agents/{id}/credentials-metadata` | Bearer | — | `AgentCredentialsMeta(cookie_names, proxy_count, updated_at)` | `404` |
| `clear_agent_credentials()` | `DELETE /api/v1/agents/{id}/credentials` | Bearer | — | `200 Agent` (flags cleared; idempotent) | `404` |
| `list_agent_data()` | `GET /api/v1/agents/{id}/data?limit&skip` | Bearer | — | `AgentDataPage(data, total)` | `404` agent gone, `422` bad params |
| `list_orphaned_data()` | `GET /api/v1/data/orphaned?limit&skip` | Bearer | — | `AgentDataPage(data, total)` | `422` bad params |
| `delete_data()` | `DELETE /api/v1/data/{id}` | Bearer | — | `None` (204, empty body) | `404` |
| `delete_data_items()` | `DELETE /api/v1/data` | Bearer | `{"ids": [...]}` (min 1) | `int` (deleted count) | `422` |
| `websocket_url(token)` | `WS /api/v1/notifications/ws?token=` | access token in query | — | `ws://`/`wss://` URL string | handshake rejection (close 1008) |

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
- `stream_agent_run` (replaces the old 202-JSON `run_agent`) is a GET with
  side effects (accepted backend quirk) returning an SSE stream: it BLOCKS on
  a worker thread until the crawl finishes, calling `on_event` with typed
  `AgentRunEvent`s — `kind == "start"` (run confirmed), `"document"`
  (`document: AgentData`, one per page as the backend SAVES it, in save
  order), `"done"` (`done: AgentRunDone` with status/runtime/count/lastRun,
  which closes the stream). Minimal SSE parsing lives here: buffer
  event/data lines, dispatch on the blank line, ignore `:`-comments
  (keep-alives); malformed frames parse into None-field events, never raise.
  `timeout=(10, None)` — an unlimited READ timeout is required (the shared
  10 s read timeout would cut the stream between keep-alives). A non-200
  status raises BEFORE any event fires, which is what makes the session's
  refresh-and-retry-on-401 safe (the crawl never started). Errors surface the
  backend detail verbatim (409 "Agent is already running"; 400 when the
  script is not a JSON object with a non-empty `links` list of URL strings —
  for `source_pages` agents, when the script violates the source_pages
  structure rules or lists non-http(s) source pages; for `ecommerce` agents,
  non-http(s) seeds, seeds over `CRAWL_MAX_PAGES`, a `max_products` over
  `ECOMMERCE_MAX_PRODUCTS`, or a structure violation). `sourceType` values:
  `"generic"` | `"facebook"` | `"source_pages"` | `"ecommerce"`
  (source_pages runs first DISCOVER article links from the script's
  `source_pages` listing pages in headless Chromium — clicking a `next_page`
  or `load_more` button, randomized 3–120 s listing delays — then crawl the
  unique links; the row stays `Running` for minutes by design, and stop works
  during discovery too. ecommerce runs crawl a product catalog in two phases
  — discover product links on the `links` listing pages, follow `next_page`
  automatically, extract built-in product fields per product — see
  `ECOMMERCE_CRAWLER_STRATEGY.md`). The camelCase `DataOut` (`agentId`,
  `agentName`, `url`, `fields` mapping XPath name → value/`null`,
  `crawledAt`) parses into `AgentData` / `AgentDataPage`; the data list sorts
  newest-first server-side. `list_orphaned_data` hits
  `GET /api/v1/data/orphaned` — same `DataOut` wire shape, so it reuses the
  same parse (orphaned docs arrive with `agentId: null`, so `agent_id` parses
  as `None`); it NEVER 404s (an empty page just means no agent has been
  deleted) and supplements `list_agent_data`, which 404s once the agent is
  gone. Orphaned docs never relink to a recreated same-name agent (the
  backend nulls `agentId` on deletion; a recreate gets a fresh id).
  `delete_data_items` sends `{"ids": [...]}` —
  unknown ids simply don't count — and reuses the deleted-count parse;
  `delete_data` is 204-never-parsed. Data docs survive agent deletion, but
  `list_agent_data` 404s once the agent is gone (the UI clears its data
  section on that 404). The backend also exposes `GET /api/v1/agents/{id}`;
  it stays unwrapped — the list plus row data covers the UI.
- Every request goes through the shared `requests.Session` with the standard
  timeout (10 s) so busy states on buttons can never wedge.
- Base URL resolution: constructor arg → `DATA_CRAWLER_API_URL` → `http://localhost:8000`.
- The notification WebSocket keeps its wire format here too:
  `websocket_url(token)` derives the `ws://` URL from `base_url` (token in the
  query string — QWebSocket cannot set handshake headers), and
  `parse_notification(dict)` turns frames into `Notification(message,
  created_at)`, returning `None` for control frames (`{"type": "connected"}`)
  so the client never shows them. Agent-run pushes parse via
  `parse_agent_status(dict)` into `AgentStatusEvent(agent_id, agent_name,
  status, created_at, runtime_seconds, count, message)` (`None` for other
  frame types; optional fields are `None` when absent — Completed carries
  `runtime_seconds` + `count`, Failed carries `runtime_seconds` + `message`).

## Errors

- Raise `ApiError(message, status_code)` for every failure — callers branch on `status_code`.
- `{"detail": "..."}` string → use it verbatim; `{"detail": [...]}` (422) →
  `"; "`-joined `field: msg` pairs; anything else → `Request failed (HTTP n)`.
- Transport failures (unreachable, timeout) carry `status_code=None` and the
  message `Could not reach the server at <base_url>`.
- A response that fails to parse into `User` / `TokenPair` raises
  `ApiError("Unexpected response from the server")` — never leak a KeyError to callers.
