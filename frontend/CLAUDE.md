# Data Crawler Frontend

PySide6 desktop client for the `data-crawler` backend (sibling `backend/`,
FastAPI + MongoDB). Login / sign-up with rotating refresh tokens; sessions
persist across restarts via QSettings (silent refresh on launch).

## Environment

- Python 3.14, virtualenv at `.venv/` (always use `.venv/bin/python` / `.venv/bin/pip`)
- PySide6 6.11.2 + requests, pinned in `requirements.txt`
- Backend expected at `http://localhost:8000`; override with `DATA_CRAWLER_API_URL`
- QSettings identity: org `DataCrawler`, app `Data Crawler` — set in `main.py`
  before the first `QSettings()` use

## Commands

```bash
.venv/bin/pip install -r requirements.txt             # install deps
.venv/bin/python main.py                              # run app (from frontend/)

# Backend (details in backend/CLAUDE.md)
cd ../backend && docker compose up -d                 # MongoDB
../backend/.venv/bin/uvicorn app.main:app --port 8000 # API
curl -s localhost:8000/api/health                     # expect {"status":"ok","database":"up"}

# Headless render check: QT_QPA_PLATFORM=offscreen + widget.grab().save("...png")
```

## Architecture

Flow: UI pages → `SessionController` signals → `run_async` (QThreadPool) →
blocking `ApiClient` (requests) → backend API. Results return to the GUI
thread via queued signals — workers never touch widgets.

```
app/
├── api/client.py               # ApiClient: all 20 endpoints, camelCase JSON, ApiError; websocket_url + parse_notification
├── core/worker.py              # run_async(): pool threads -> queued signals (GUI thread)
├── core/session.py             # SessionController: tokens, refresh, QSettings, forced logout
├── core/notifications.py       # NotificationClient: QWebSocket stream + 5 s reconnect (GUI thread)
├── ui/widgets.py               # PasswordLineEdit, chosen_combo (macOS-safe combos)
├── ui/login_page.py            # centered Sign in / Create account card (no header)
├── ui/dashboard_page.py        # placeholder text + admin-only User management card
├── ui/settings_page.py         # profile card + Change password button
├── ui/agent_management_page.py # all users: landing page — splitter-stacked agent table (add/edit/delete/run) + per-agent data table
├── ui/user_management_page.py  # admin: paginated user table, role/status combos, delete
├── ui/change_password_dialog.py  # modal dialog (3 PasswordLineEdit fields)
├── ui/agent_dialog.py          # modal Add/Edit agent form (script editor is the source of truth; json adds key-value rows + Generate JSON)
├── ui/agent_data_dialog.py     # read-only modal showing one crawled record (URL subtitle, crawled date, pretty-JSON fields viewer)
├── ui/confirm_dialog.py        # ConfirmDialog.ask(): styled yes/no card (danger variant)
├── ui/format.py                # format_date / format_role / format_status / format_time
├── ui/main_page.py             # header (Dashboard + Agents nav left, bell + account email menu right)
├── ui/main_window.py           # QMainWindow: Loading / Login / Main stack; owns NotificationClient
└── resources/
    ├── style.qss               # the only stylesheet (objectName selectors)
    └── icons/                  # eye, eye-off, chevron-down, trash, check, plus, edit, play, plus-neutral, minus-neutral, bell (.svg)
```

## Golden Rules

- Workers never touch widgets — results cross threads only via queued signals (`app/core/worker.py`)
- All `QSettings` I/O happens on the GUI thread (session emits `tokens_rotated` for writes)
- The backend rotates BOTH tokens on every refresh — single-flight in
  `SessionController._rotate_tokens`; never replay a refresh token (revokes the family)
- 401 → refresh once → retry once → forced logout if that fails
- camelCase JSON lives only in `app/api/client.py`; everything else sees `User` /
  `TokenPair` / `UserPage` / `Agent` / `AgentPage` / `AgentData` /
  `AgentDataPage` / `Notification`
  (WS frames parse via `parse_notification`; `websocket_url(token)` builds the
  stream URL with the access token in the query string)
- Client validation mirrors the backend (username 3–32 `^[a-zA-Z0-9_.-]+$`,
  password 8–64 chars / ≤72 UTF-8 bytes, email format; agent name 1–100 chars
  stripped, script 1–1,000,000 chars — the script editor's text is submitted
  in every format; json mode adds a `json.loads` check and a Generate JSON
  button that writes validated key-value rows into the editor) and blocks the
  request
- The login page never shows a header; the header lives only in `MainPage`
- The header nav is Dashboard + Agents, both for every authenticated user;
  Settings + Log out live in the account menu behind the email button. The
  User management entry is admin-only (Dashboard card gated on
  `user.role == "admin"`). Every session start (login, sign-up, silent
  refresh) lands on the Agents page — `MainPage.set_user` switches there and
  reloads it; `reset()` preselects it for the next login
- A bell button (`#notificationButton`) sits left of the account button:
  pushed notifications land in `QMenu#notificationMenu` (newest-first, capped
  at 20, `· HH:MM` via `format_time`), unread state via the `[unread="true"]`
  QSS attribute + repolish. `NotificationClient` (QWebSocket, GUI thread, 5 s
  reconnect, refresh-on-handshake-rejection) is owned by `MainWindow` and
  started/stopped by `session_started` / `session_ended`
- Admin role/status edits go through the per-row combos in
  `UserManagementPage`; the current user's own row is plain text (the backend
  rejects self-changes)
- Deletes: per-row trash button (single, 204) or row checkboxes + the
  `Delete selected (N)` danger button (bulk `{"userIds": [...]}`); every delete
  passes `ConfirmDialog.ask(..., danger=True)` first. One delete in flight at a
  time, mutually exclusive with a pending combo change; the admin's own row has
  no checkbox and no trash button (the backend rejects self-deletes)
- Agents are created/edited in `AgentDialog` (validation mirrors the backend);
  the script never renders in the table (up to 1 MB) — the dialog owns it.
  Agent deletes confirm via `ConfirmDialog.ask(..., danger=True)` and run one
  at a time; an accepted create resets the page to page 1 (newest-first)
- AgentManagementPage layout: the "Add agent" primaryButton sits right of
  the "Agent management" pageTitle (no toolbar row); the agents table and
  the data section stack in a vertical `#agentsSplitter` — drag the handle
  to reallocate height (panes never collapse; a one-shot `showEvent` seeds
  the old 3:2 split). There is no "Agent data" section title — the data
  section's `#pageSubtitle` shows only the empty-state caption (it blanks
  once an agent is selected). Row counts render as "of N" beside every
  `#limitSelector`; no "N users / N agents / N records" count subtitles
- A per-row Run button (no confirm; `#rowRunButton` play.svg) starts the
  agent's crawl — 202 on a GET-with-side-effects — and reloads the table
  (status flips to Running, button disables); 409/400/404 land in the banner
  verbatim. Clicking an agent name (indigo link) selects it: the lower
  splitter pane's `#dataTable` shows its crawled records (own
  banner/progress/bulk bar/pagination, busy flags independent of the agents
  section) with per-row + checkbox bulk deletes (confirm, danger, one in
  flight); a data-list 404 or deleting the selected agent clears the
  section. Fields cells are indigo links opening a read-only
  `AgentDataDialog` ("—" cells are inert); the `#pageButton` Hide/Show sits
  in the data bulk bar next to "Delete selected" and collapses only the
  table + pagination (`_data_collapsible`) so the bulk bar — and the Show
  button — stay visible; a name click always re-expands; clearing the
  section resets it
- Styling only via `app/resources/style.qss` — no `setStyleSheet` in code;
  QSS `image:` urls use the `%icons%` placeholder, substituted with the
  absolute icons path in `main.py` (QSS resolves urls against the CWD)
- Verify with the checklist in `.claude/rules/verification.md` before finishing

## Rule Files

Topic-specific conventions live in `.claude/rules/`:

| File | Topic | Loads |
|---|---|---|
| `api-client.md` | Endpoint inventory, JSON contract, error normalization | when editing `app/api/**` |
| `auth-session.md` | Token model, refresh invariants, session persistence | every session |
| `qt-threading.md` | Worker/signal discipline, QSettings thread rule | when editing `app/core/**` or `app/ui/**` |
| `ui-conventions.md` | Page structure, styling, input validation | when editing `app/ui/**` or `app/resources/**` |
| `verification.md` | End-to-end smoke-test workflow | every session |
