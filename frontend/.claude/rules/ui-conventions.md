---
description: Page structure, header ownership, stylesheet rules, PasswordLineEdit, and client-side validation for app/ui.
globs: ["app/ui/**", "app/resources/**"]
---

# UI Conventions Rules

## Structure

- `MainWindow` holds one `QStackedWidget`: Loading (0) / Login (1) / Main (2);
  session signals drive the current page, nothing else.
- `QStackedWidget` only lays out its current page — make a page current before
  measuring geometry (offscreen tests included).
- LoginPage: centered card, no header, ever. MainPage owns the only header —
  nav (`Dashboard`, `Agents`) left, account button (user email + chevron)
  right. The account button opens a `QMenu` (`#accountMenu`) with Settings
  and Log out; there is no standalone logout button.
- MainPage's body stack: DashboardPage / AgentManagementPage / SettingsPage /
  UserManagementPage. Each nav button is checked only while its page is
  current.
- DashboardPage always shows `This is the Dashboard page`; an admin
  (`user.role == "admin"`) additionally gets the User management card, which
  is the only entry point to UserManagementPage.
- SettingsPage: profile card (username, email, role, status, created via
  `app/ui/format.py`) + Change password → `ChangePasswordDialog` (modal,
  three `PasswordLineEdit` fields, busy + error/success banners).
- UserManagementPage (admin): `QTableWidget#usersTable` with 7 columns —
  checkbox, Username, Email (the Stretch column), Role, Status, Created, and a
  per-row trash button. Role/status are per-row `QComboBox` cell widgets; the
  trash `QPushButton#rowDeleteButton` (28×28, `QIcon` trash.svg) deletes one
  user. No selection, no sorting (server sorts newest-first), alternating
  rows. The current user's own row renders plain text and empty guarded cells
  with tooltips (the backend rejects self-changes AND self-deletes — no
  checkbox, no trash button there). A toolbar row above the table holds
  `QCheckBox#selectAllCheckBox` (checks all eligible rows on the page) and
  `QPushButton#dangerButton` "Delete selected (N)" (disabled at 0). Every
  delete goes through `ConfirmDialog.ask(..., danger=True)`
  (`app/ui/confirm_dialog.py`) first; one delete in flight at a time,
  mutually exclusive with a pending combo change; success → `reload()` (the
  shrink-refetch clamps an emptied last page), error → banner + `reload()`.
  Pagination: `QComboBox#limitSelector` (10/25/50/100), Previous/Next,
  `Page X of Y`; changing the limit resets to page 1; one role/status change
  in flight at a time, reverting the combo if the server refuses.
- AgentManagementPage (every authenticated user): `QTableWidget#agentsTable`
  with 8 columns — Name (the Stretch column), Format, Status, Created,
  Updated, Updated by, and per-row edit + trash buttons
  (`QPushButton#rowEditButton` edit.svg / `#rowDeleteButton` trash.svg,
  28×28). The script is never a column (up to 1,000,000 chars) — the dialog
  owns it. A toolbar row holds the `primaryButton` "Add agent" (plus.svg);
  every delete passes `ConfirmDialog.ask(..., danger=True)`; one delete in
  flight at a time; success → `reload()` (clamps an emptied last page),
  error → banner + `reload()`. Pagination matches the users page; an accepted
  create resets to page 1 (newest-first), an accepted edit reloads the
  current page.
- AgentDialog (modal, `#agentDialog`, fixed width 560, height refits via
  `adjustSize()`): Name `QLineEdit` (max 100), Format
  `chosen_combo("formatCombo")` (JSON/XML/Markdown → json/xml/md), and a
  script section sharing one "Script" caption. `QPlainTextEdit#scriptEdit`
  (fixed 240px) is visible in EVERY format and is the submitted script — the
  editor is the source of truth. JSON adds a key-value builder above it: an
  `#addRowButton` plus beside the caption adds a key group,
  `QLabel#columnCaption` "key"/"value" captions over the row grid, groups
  laid out `[key][remove-key][value][add-value][remove-value]` in a
  `QGridLayout` (stretches 1,0,1,0,0; 24px fixed button columns keep the
  captions aligned); extra values stack under the value column with their own
  minus; removing a key's last value removes the group. Rows live in
  `self._entries` (key `QLineEdit` + value `QLineEdit`s + their buttons);
  mutations relayout the grid but never recreate kept widgets; rows scroll in
  an `OverlayScrollArea` that is a FIXED 160px slot — add/remove never resizes
  the dialog, only the mode switch refits. A plain "Generate JSON"
  `QPushButton` between the rows and the editor is the only writer of
  generated JSON: rows → `_validate_rows` (no non-blank rows, a value without
  a key, a key with no value, duplicate keys — each errors in the banner and
  writes nothing) → `json.dumps(..., ensure_ascii=False, indent=2)` (single
  value → string, multiple → array) into the editor; hand edits in the editor
  are never auto-overwritten. Submit validates the EDITOR text in every
  format: non-empty, ≤1,000,000 chars, plus a `json.loads` check in json mode
  (hand edits can break it — mirror of the backend 400). Switching the combo
  into json repopulates rows from the editor (`_script_to_entries`,
  empty/unparseable → empty rows); switching out is a no-op (the editor
  already holds the script). It closes immediately on success (the reloaded
  table row is the feedback).

## Inputs & validation

- Password fields are always `PasswordLineEdit` (`app/ui/widgets.py`) — the
  eye / eye-slash trailing action toggles echo mode.
- Validation mirrors the backend and blocks the request: username 3–32 chars
  `^[a-zA-Z0-9_.-]+$`, password 8–64 chars and ≤72 UTF-8 bytes, email regex;
  agent name 1–100 chars (stripped), script 1–1,000,000 chars — in json mode
  the script must also parse (`json.loads`); generated JSON comes only from
  the Generate button's validated key-value rows.
- Submit buttons disable + relabel while busy (`set_busy`); errors render in
  the banner labels; warnings (environment) intentionally survive `reset()`.

## Styling

- All styling lives in `app/resources/style.qss`, selected by `objectName` —
  never call `setStyleSheet` on a widget.
- Palette: window `#F5F6FA`, surfaces `#FFFFFF`, text `#1F2430` / `#6B7280`,
  accent indigo `#4F46E5`, danger `#DC2626`, border `#E2E4EA`. The script
  editor deviates: `#F3F4F6` surface with black text (a distinct code surface).
- SVG icons hardcode their stroke color — `currentColor` does not survive
  QIcon's SVG renderer.
- QSS `image:` urls must use the `%icons%` placeholder (e.g. the combo
  `down-arrow` chevron); `main.py` substitutes the absolute icons path
  because QSS resolves relative urls against the process CWD. Pure-QSS
  border-triangle arrows render as a dash — don't retry that trick.
- QMenu rounded corners need `WA_TranslucentBackground` set in code.
- Combos use `chosen_combo()` from `app/ui/widgets.py` (Fusion style + accent
  delegate — QMacStyle squeezes the popup); `QPlainTextEdit` is styled via
  `#scriptEdit`. Dialog row mini-buttons are `#addRowButton`/`#removeRowButton`
  with `plus-neutral.svg`/`minus-neutral.svg` (gray icons — the white
  `plus.svg` is only for the indigo primaryButton).
- Use scoped enums (`Qt.AlignmentFlag.AlignCenter`) and valid constructor
  property kwargs (`objectName=`, `placeholderText=` — there is no `placeholder=`).
