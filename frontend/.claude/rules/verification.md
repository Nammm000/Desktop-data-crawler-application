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
- Agents reload button: the icon-only reload button sits right of the
  "Agent management" title (tooltip "Reload agents"); clicking it refetches
  the table and the selected agent's data pane (e.g. a status flipped
  elsewhere updates without page re-entry); it disables while a fetch runs
  and re-enables after; the No-agent data button stays enabled during a
  fetch (unchanged); while signed out it does nothing
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
  banner; agent deleted via curl → "Agent not found" + resync; the row
  flips to Completed/Failed live when the WS agentStatus push lands and the
  Agents page is visible (also flips to Running live for curl/other-user
  runs); on any other page the status appears on re-entry
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
- No-agent data page: "No-agent data" `#pageButton` sits left of "Add agent"
  on the Agents page and stays clickable during an agents fetch; clicking it
  shows the page (neither header nav checked) with records whose agent was
  deleted (seed by running an agent then deleting it — or use docs already
  orphaned; to prove the backend's startup sweep, mongosh-insert a doc with a
  bogus string `agentId` and restart uvicorn); Agent column is plain text with the
  deleted-agent tooltip; Fields links open `AgentDataDialog`, "—" cells are
  inert; per-row trash + checkbox bulk deletes confirm (Cancel keeps rows)
  and update the "of N" count; emptied last page clamps; limit selector
  resets to page 1, Previous/Next respect bounds; "Back to agents" returns
  with the Agents nav checked and the agents page reloaded; re-entering the
  page refetches (delete an agent elsewhere → its docs appear on next
  entry); backend down → banner + no wedge; logout clears the page
- Notifications (temporary 15-min push; shorten with
  `NOTIFICATION_INTERVAL_SECONDS=3` on uvicorn): bell button left of the
  email button, no unread tint at login; a push flips the bell to the indigo
  unread tint; clicking the bell clears unread and lists `15 minutes have
  passed · HH:MM` newest-first (dark informational text; "No notifications"
  when empty); log out → socket closed, list cleared, unread cleared, no
  reconnect attempts; kill uvicorn mid-session → app stays responsive, no
  wedge; restart uvicorn → the socket reconnects and a new push arrives
  (garbage token at the handshake → rejected, close 1008 / HTTP 403)
- Agent-status pushes (crawl outcomes): a finished crawl flips the bell to
  unread and lists `Agent "X" completed in N s (M records) · HH:MM` /
  `Agent "X" failed after N s · HH:MM` newest-first; the Agents page (when
  visible) refreshes itself — row status, Updated/Updated by, and the
  selected agent's data pane pick up the outcome with no manual reload; a
  run finishing while Dashboard/Settings is showing adds only the bell entry
  (the row updates on re-entry); log out before completion → no bell entry,
  no refresh; a fast crawl finishing while the run-triggered fetch is still
  in flight must NOT leave the row on Running (the deferred refresh flushes
  when the fetch settles); restart uvicorn mid-session → the page resyncs
  on reconnect (a Running row flips to Failed via the startup sweep without
  page re-entry)

## Source-pages agents (sourceType "source_pages")

- Add/Edit agent dialog: the Source combo offers "Source pages (listing to
  articles)"; selecting it shows the reserved-keys hint and hides the facebook
  credentials block (and vice versa)
- A json script violating the structure (both `next_page` and `load_more`,
  missing `source_pages` or `post_link`, `max_next` non-integer/`true`,
  a field with a number value) is blocked client-side with the backend's
  exact detail message — no request is sent
- A valid source_pages script saves and runs; the row flips to Running
  (source_pages runs take minutes by design — run uvicorn with
  `SOURCE_PAGES_DELAY_MIN_SECONDS=0.5 SOURCE_PAGES_DELAY_MAX_SECONDS=2` to
  speed up manual testing), then Completed via the WS push with records in
  the data pane; the name tooltip reads "Source-pages agent (listing
  discovery + article crawl)"
- Stop mid-discovery (large SOURCE_PAGES_DELAY_MAX_SECONDS): the agent lands
  Stopped with a `cancelled` lastRun entry; no chromium process survives
  (`pgrep -f Chromium` clean)

## E-commerce agents (sourceType "ecommerce")

- Add/Edit agent dialog: the Source combo offers "E-commerce products
  (listing to products)"; selecting it shows the script-keys hint (links +
  optional product_link/next_page/max_next/max_products + field overrides)
  and hides the facebook credentials block (and vice versa)
- A json script violating the structure (missing/empty `links`, both
  `max_next` spellings, `max_products` non-integer/`true`, a field with a
  number value) is blocked client-side with the backend's exact detail
  message — no request is sent
- A valid ecommerce script saves and runs against books.toscrape.com (no
  browser install needed; ~1 s per page — the Travel category's 11 products
  finish in well under a minute): the row flips to Running, then Completed
  via the WS push with 11 records in the data pane — every field populated
  (`price` numeric like "26.08", `currency` "GBP", `rating` "1"–"5",
  `imageUrl` absolute); the name tooltip reads "E-commerce product agent
  (listing + product pages)"; `lastRun.totalLinks` is 11
- Pagination + caps: the Nonfiction category (110 books) with
  `{"max_next": 2, "max_products": 25}` completes with exactly 25 records,
  `totalLinks` 25, and one `cancelled` "max_products" failure entry; a
  `max_products` above `ECOMMERCE_MAX_PRODUCTS` (default 100) is a 400 at
  run time
- Stop mid-crawl: the agent lands Stopped; partial products are kept and a
  `cancelled` entry counts the unfetched discovered products
