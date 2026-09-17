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
- Header: Dashboard nav only; email button opens the account menu (Settings /
  Log out); Settings/Log out work from the menu
- Settings page shows username/email/role/status/created; Change password
  dialog: wrong current → backend 403 message; same/short/mismatched password
  blocked client-side; success → green banner, auto-close, session survives,
  new password works on next sign-in
- Dashboard: admin sees the User management card; non-admin sees only
  `This is the Dashboard page` (create a non-admin by flipping role in mongosh
  — every signup is admin — then re-login)
- User management (admin): table newest-first; own row plain text with
  tooltip; role/status combos change values (ban → target's next login 403);
  errors (backend down, self-change attempts) show in the banner and revert
  the combo; limit selector resets to page 1; Previous/Next respect bounds;
  `Page X of Y` matches `total`
