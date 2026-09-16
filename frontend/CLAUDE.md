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
├── api/client.py               # ApiClient: all 7 endpoints, camelCase JSON, ApiError
├── core/worker.py              # run_async(): pool threads -> queued signals (GUI thread)
├── core/session.py             # SessionController: tokens, refresh, QSettings, forced logout
├── ui/widgets.py               # PasswordLineEdit (eye / eye-slash toggle)
├── ui/login_page.py            # centered Sign in / Create account card (no header)
├── ui/main_page.py             # header (nav left, email + logout right) + placeholder pages
├── ui/main_window.py           # QMainWindow: Loading / Login / Main stack
└── resources/
    ├── style.qss               # the only stylesheet (objectName selectors)
    └── icons/                  # eye.svg, eye-off.svg (hardcoded stroke color)
```

## Golden Rules

- Workers never touch widgets — results cross threads only via queued signals (`app/core/worker.py`)
- All `QSettings` I/O happens on the GUI thread (session emits `tokens_rotated` for writes)
- The backend rotates BOTH tokens on every refresh — single-flight in
  `SessionController._rotate_tokens`; never replay a refresh token (revokes the family)
- 401 → refresh once → retry once → forced logout if that fails
- camelCase JSON lives only in `app/api/client.py`; everything else sees `User` / `TokenPair`
- Client validation mirrors the backend (username 3–32 `^[a-zA-Z0-9_.-]+$`,
  password 8–64 chars / ≤72 UTF-8 bytes, email format) and blocks the request
- The login page never shows a header; the header lives only in `MainPage`
- Styling only via `app/resources/style.qss` — no `setStyleSheet` in code
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
