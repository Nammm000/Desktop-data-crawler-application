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
- Header: Dashboard + Agents nav (both for every user); email button opens the
  account menu (Settings / Log out); Settings/Log out work from the menu
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
- User management deletes (admin): per-row trash button → styled confirm card,
  Cancel leaves the row intact, confirm removes it and updates the `N users`
  subtitle; row checkboxes drive `Delete selected (N)` (bulk) and Select all
  never touches the own row (no checkbox/trash there, tooltip present);
  deleted user's next login fails; deleting every row of the last page clamps
  the page index (no empty "Page 2 of 1"); a delete in flight disables the
  table/pagination and blocks combo changes (and vice versa); delete while the
  backend is down → banner + resync, UI never wedges; deleting an
  already-removed user (second admin/curl) → "User not found" banner + resync
- Agents (all users, incl. non-admin): Agents nav checks/unchecks in sync with
  Dashboard and re-entering the page reloads it; table newest-first; `N agents`
  subtitle; the script never appears in the table; limit selector resets to
  page 1; Previous/Next respect bounds; `Page X of Y` matches `total`
- Add agent: empty name, empty script (any format), >100-char name, and a
  text-mode script over 1,000,000 chars blocked client-side (no request
  sent); success closes the dialog immediately and lands on page 1 with the
  new row; duplicate name → backend 409 message in the dialog banner
- Agent dialog JSON mode: the header plus adds a key group; a group's plus
  adds a value line under the value column (no key input there); a value's
  minus removes just that value and the last value's minus (or the key's
  minus) removes the whole group; the script editor is visible in every
  format and holds the submitted script. Generate JSON writes pretty JSON to
  the editor (single value → `"key": "value"`, multiple → an array); zero
  non-blank rows, blank key with a value, key with all-blank values, and
  duplicate keys each block Generate with their banner error and leave the
  editor untouched; hand-edited JSON in the editor is what's submitted
  verbatim (valid → accepted, invalid → client-side "not valid JSON" error,
  no request); switching Markdown → JSON repopulates the rows from the
  editor's JSON and switching out leaves the editor alone; a busy submit
  disables every row edit, every plus/minus button, and Generate
- Edit agent: modal prefilled; JSON agents parse their stored script into
  rows in stored order (string, array, scalar, nested per the best-effort
  rules) and the editor shows the stored script verbatim (every format);
  format change json → md accepts a non-JSON script; Updated /
  Updated by refresh after save; rename to an existing name → 409 banner
- Agent deletes: per-row trash → styled confirm card; Cancel keeps the row;
  confirm removes it and updates the subtitle; deleting every row of the last
  page clamps the page index; delete while the backend is down → banner +
  resync; deleting an already-removed agent (second user/curl) → "Agent not
  found" banner + resync
