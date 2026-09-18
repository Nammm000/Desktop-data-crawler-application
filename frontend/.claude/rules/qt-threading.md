---
description: Worker-thread and queued-signal discipline plus the QSettings GUI-thread rule for app/core and app/ui.
globs: ["app/core/**", "app/ui/**"]
---

# Qt Threading Rules

## Workers

- Never block the GUI thread — every network call goes through
  `run_async(fn, on_success, on_error)` in `app/core/worker.py`.
- Workers are plain blocking functions: they never touch widgets and never
  read or write `QSettings`.
- Results cross to the GUI thread via queued `Signal(object)` connections —
  slots run on the GUI thread even when emitted from a pool thread.
- New async work follows the same shape: blocking closure plus success/error
  callbacks; the 10 s request timeout stays the worst-case bound.

## GUI thread

- `SessionController` exposes GUI-thread entry points (`bootstrap`,
  `login_and_start`, `signup_and_start`, `logout`, `fetch_current_user`,
  `change_password`, plus the notification-stream helpers
  `current_access_token` / `refresh_access_token`) that spawn `run_async`
  work — UI code calls only these, never `ApiClient` directly.
- QSettings is not thread-safe: persistence happens only in slots connected
  to `tokens_rotated` / `_clear_tokens_requested`.

## WebSocket (notifications)

- `NotificationClient` (`app/core/notifications.py`) uses Qt's built-in
  `QWebSocket` — event-loop native, so it runs entirely on the GUI thread:
  signals in, signals out, no `run_async`, no worker thread, no QSettings.
- Reconnects on a 5 s single-shot `QTimer`; a handshake rejection with an
  unchanged token triggers one `refresh_access_token()` (single-flight; a 401
  refresh failure force-logs-out, transport failures just retry later).
