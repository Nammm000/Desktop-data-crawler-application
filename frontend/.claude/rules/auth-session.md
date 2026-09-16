---
description: Token rotation, single-flight refresh, QSettings persistence, and forced-logout invariants for SessionController.
alwaysApply: true
---

# Auth & Session Rules

## Token model

- **Access token**: JWT, 15 min, sent as `Authorization: Bearer`. Kept in memory only.
- **Refresh token**: opaque string, 7 days, travels in the JSON body only. Persisted
  in QSettings key `auth/refreshToken` so sessions survive restarts.
- `bootstrap()` on launch: stored token → `refresh()` → `get_current_user()` →
  main screen; any failure → clear storage → login page.

## Invariants — must survive any refactor

- The backend rotates BOTH tokens on every refresh — adopt the whole `TokenPair`
  via `_apply_pair` and persist the new refresh token (`tokens_rotated` signal).
- Refresh is single-flight under `threading.Lock` with stale-token comparison:
  a caller whose access token already rotated reuses the new one instead of
  replaying the old refresh token — replay revokes the user's entire token family.
- `_authorized_call`: on 401 → refresh once → retry once. A second 401 or a failed
  refresh forces logout (clear tokens + QSettings, `session_ended` with a message).
- Signup returns a `User`, not tokens — always follow `signup()` with `login()`.
- Logout is best-effort on the API (failures swallowed — the token expires
  server-side) but ALWAYS clears local state and emits `session_ended("")`.
- All QSettings reads/writes happen on the GUI thread; worker threads go through
  the `tokens_rotated` / `_clear_tokens_requested` signals.
