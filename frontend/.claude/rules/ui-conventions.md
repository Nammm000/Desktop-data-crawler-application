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
  nav (`Dashboard`) left, account button (user email + chevron) right. The
  account button opens a `QMenu` (`#accountMenu`) with Settings and Log out;
  there is no standalone logout button.
- MainPage's body stack: DashboardPage / SettingsPage / UserManagementPage.
  The Dashboard nav button is checked only while the Dashboard is current.
- DashboardPage always shows `This is the Dashboard page`; an admin
  (`user.role == "admin"`) additionally gets the User management card, which
  is the only entry point to UserManagementPage.
- SettingsPage: profile card (username, email, role, status, created via
  `app/ui/format.py`) + Change password → `ChangePasswordDialog` (modal,
  three `PasswordLineEdit` fields, busy + error/success banners).
- UserManagementPage (admin): `QTableWidget#usersTable` (Username, Email,
  Role, Status, Created) with per-row role/status `QComboBox` cell widgets —
  the combos ARE the actions, no separate Actions column. No selection, no
  sorting (server sorts newest-first), alternating rows. The current user's
  own row renders plain text (the backend rejects self-changes). Pagination:
  `QComboBox#limitSelector` (10/25/50/100), Previous/Next, `Page X of Y`;
  changing the limit resets to page 1; one role/status change in flight at a
  time, reverting the combo if the server refuses.

## Inputs & validation

- Password fields are always `PasswordLineEdit` (`app/ui/widgets.py`) — the
  eye / eye-slash trailing action toggles echo mode.
- Validation mirrors the backend and blocks the request: username 3–32 chars
  `^[a-zA-Z0-9_.-]+$`, password 8–64 chars and ≤72 UTF-8 bytes, email regex.
- Submit buttons disable + relabel while busy (`set_busy`); errors render in
  the banner labels; warnings (environment) intentionally survive `reset()`.

## Styling

- All styling lives in `app/resources/style.qss`, selected by `objectName` —
  never call `setStyleSheet` on a widget.
- Palette: window `#F5F6FA`, surfaces `#FFFFFF`, text `#1F2430` / `#6B7280`,
  accent indigo `#4F46E5`, danger `#DC2626`, border `#E2E4EA`.
- SVG icons hardcode their stroke color — `currentColor` does not survive
  QIcon's SVG renderer.
- QSS `image:` urls must use the `%icons%` placeholder (e.g. the combo
  `down-arrow` chevron); `main.py` substitutes the absolute icons path
  because QSS resolves relative urls against the process CWD. Pure-QSS
  border-triangle arrows render as a dash — don't retry that trick.
- QMenu rounded corners need `WA_TranslucentBackground` set in code.
- Use scoped enums (`Qt.AlignmentFlag.AlignCenter`) and valid constructor
  property kwargs (`objectName=`, `placeholderText=` — there is no `placeholder=`).
