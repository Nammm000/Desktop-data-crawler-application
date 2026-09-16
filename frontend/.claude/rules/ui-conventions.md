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
  nav (`Dashboard`, `Settings`) left in an exclusive `QButtonGroup`,
  user email + Log out right.
- Placeholder pages show exactly `This is the Dashboard page` /
  `This is the Settings page` until real content exists.

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
- Use scoped enums (`Qt.AlignmentFlag.AlignCenter`) and valid constructor
  property kwargs (`objectName=`, `placeholderText=` — there is no `placeholder=`).
