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

# 4. Notification stream: run uvicorn with a shortened interval instead of
#    waiting 15 minutes:
NOTIFICATION_INTERVAL_SECONDS=3 ../backend/.venv/bin/uvicorn app.main:app --port 8000
```

Checklist before finishing any change (covers every endpoint):

- Cold start (no stored token) → centered login card, no header
- Eye icon hides/shows the password on both forms
- Invalid input (short/bad-char username, 7-char or >72-byte password) blocks submit
- Sign up → main screen; email top-right; lands on the Agents page (Dashboard
  nav shows the placeholder page)
- Duplicate email/username → backend message in the error banner
- Quit + relaunch → loading → straight to main on Agents (silent refresh +
  `/users/me`)
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
  Cancel leaves the row intact, confirm removes it and updates the "of N"
  count beside the limit selector; row checkboxes drive `Delete selected (N)`
  (bulk) and Select all
  never touches the own row (no checkbox/trash there, tooltip present);
  deleted user's next login fails; deleting every row of the last page clamps
  the page index (no empty "Page 2 of 1"); a delete in flight disables the
  table/pagination and blocks combo changes (and vice versa); delete while the
  backend is down → banner + resync, UI never wedges; deleting an
  already-removed user (second admin/curl) → "User not found" banner + resync
- Agents (all users, incl. non-admin): Agents nav checks/unchecks in sync with
  Dashboard and re-entering the page reloads it; table newest-first; "of N"
  count beside the limit selector (no count subtitle); the script never
  appears in the table; limit selector resets to
  page 1; Previous/Next respect bounds; `Page X of Y` matches `total`
- Agents page divider: the "Add agent" button sits top-right beside the
  "Agent management" title; the splitter handle between the agents section
  and the data section drags to reallocate their heights (line turns indigo
  on hover; neither pane collapses fully; the split survives page switches)
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
- Agent run: per-row play button (no confirm) → the row flips to Running and
  the button disables ("already running" tooltip); a curl run while the app
  still shows New → 409 banner + resync; agent whose script is not a JSON
  object with `links` (e.g. `{"nope":1}` or an md script) → 400 detail
  banner; agent deleted via curl → "Agent not found" + resync; Completed /
  Failed statuses appear on page re-entry (no live updates by design)
- Agent data section: clicking an agent name (indigo underlined) → the
  section appears (subtitle goes blank) with its own "of N" count beside
  the data limit selector (no "Agent data" heading anywhere); URL / Fields
  (single-line JSON, `null` for unmatched XPaths, "—" when empty) /
  Crawled columns; a different name → its page 1; the same name again →
  refetch; re-entering the page refreshes the data; limit selector resets to
  page 1, Previous/Next respect bounds, `Page X of Y` matches `total`
- Agent data Fields viewer: non-empty Fields cells are indigo underlined
  links; clicking opens the read-only modal (URL subtitle, Crawled date,
  pretty monospace JSON); Close and Esc dismiss; the text is
  selectable/copyable but not editable; "—" cells are plain and do nothing
  on click; a fetch/delete in flight disables the table (no mid-flight click)
- Agent data Hide/Show: the Hide button in the data bulk bar (next to
  "Delete selected", only reachable with a selection) collapses the table +
  pagination while the subtitle/banner/progress/bulk bar stay visible (so
  the flipped "Show" button stays clickable); Show restores it; clicking
  an agent name while collapsed re-expands (button reads "Hide"); the
  data-list 404 / deleting the selected agent / logout reset the section,
  hide the bulk bar + table, and clear the collapse state
- Agent data deletes: per-row trash → confirm card; Cancel keeps the row;
  checkboxes drive `Delete selected (N)`; Select all syncs; deleting every
  row of the last page clamps the page index; backend down → banner + resync;
  agent deleted via curl then a data pagination click → section clears with
  "The selected agent no longer exists."; deleting the selected agent in the
  table clears the section; the two sections stay independently interactive
  (one loading never freezes the other); logout clears selection + section
- Notifications (temporary 15-min push; shorten with
  `NOTIFICATION_INTERVAL_SECONDS=3` on uvicorn): bell button left of the
  email button, no unread tint at login; a push flips the bell to the indigo
  unread tint; clicking the bell clears unread and lists `15 minutes have
  passed · HH:MM` newest-first (dark informational text; "No notifications"
  when empty); log out → socket closed, list cleared, unread cleared, no
  reconnect attempts; kill uvicorn mid-session → app stays responsive, no
  wedge; restart uvicorn → the socket reconnects and a new push arrives
  (garbage token at the handshake → rejected, close 1008 / HTTP 403)
