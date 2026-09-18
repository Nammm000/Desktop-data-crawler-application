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
├── api/client.py               # ApiClient: all 16 endpoints, camelCase JSON, ApiError
├── core/worker.py              # run_async(): pool threads -> queued signals (GUI thread)
├── core/session.py             # SessionController: tokens, refresh, QSettings, forced logout
├── ui/widgets.py               # PasswordLineEdit, chosen_combo (macOS-safe combos)
├── ui/login_page.py            # centered Sign in / Create account card (no header)
├── ui/dashboard_page.py        # placeholder text + admin-only User management card
├── ui/settings_page.py         # profile card + Change password button
├── ui/agent_management_page.py # all users: paginated agent table, add/edit/delete
├── ui/user_management_page.py  # admin: paginated user table, role/status combos, delete
├── ui/change_password_dialog.py  # modal dialog (3 PasswordLineEdit fields)
├── ui/agent_dialog.py          # modal Add/Edit agent form (script editor is the source of truth; json adds key-value rows + Generate JSON)
├── ui/confirm_dialog.py        # ConfirmDialog.ask(): styled yes/no card (danger variant)
├── ui/format.py                # format_date / format_role / format_status
├── ui/main_page.py             # header (Dashboard + Agents nav left, account email menu right)
├── ui/main_window.py           # QMainWindow: Loading / Login / Main stack
└── resources/
    ├── style.qss               # the only stylesheet (objectName selectors)
    └── icons/                  # eye, eye-off, chevron-down, trash, check, plus, edit, plus-neutral, minus-neutral (.svg)
```

## Golden Rules

- Workers never touch widgets — results cross threads only via queued signals (`app/core/worker.py`)
- All `QSettings` I/O happens on the GUI thread (session emits `tokens_rotated` for writes)
- The backend rotates BOTH tokens on every refresh — single-flight in
  `SessionController._rotate_tokens`; never replay a refresh token (revokes the family)
- 401 → refresh once → retry once → forced logout if that fails
- camelCase JSON lives only in `app/api/client.py`; everything else sees `User` /
  `TokenPair` / `UserPage` / `Agent` / `AgentPage`
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
  `user.role == "admin"`)
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
