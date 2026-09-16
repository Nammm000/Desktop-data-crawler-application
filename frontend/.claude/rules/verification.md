---
description: End-to-end verification workflow — backend up, app run, offscreen smoke test, per-endpoint checklist.
alwaysApply: true
---

# Verification Rules

```bash
# 1. Backend up (Mongo must be healthy before uvicorn)
cd ../backend && docker compose up -d
../backend/.venv/bin/uvicorn app.main:app --port 8000
curl -s localhost:8000/api/health        # {"status":"ok","database":"up"}

# 2. App (from frontend/)
.venv/bin/python main.py

# 3. Headless smoke test: QT_QPA_PLATFORM=offscreen, drive the real widgets
#    via QTimer phases inside app.exec(), inspect window.grab().save("...png").
```

Checklist before finishing any change (covers every endpoint):

- Cold start (no stored token) → centered login card, no header
- Eye icon hides/shows the password on both forms
- Invalid input (short/bad-char username, 7-char or >72-byte password) blocks submit
- Sign up → main screen; email top-right; nav switches the two placeholders
- Duplicate email/username → backend message in the error banner
- Quit + relaunch → loading → straight to main (silent refresh + `/users/me`)
- Log out → login page; stored token cleared; relaunch stays on login
- Backend down → amber warning banner, app still usable
- `change-password` (no UI yet): verify via curl per `backend/.claude/rules/verification.md`
