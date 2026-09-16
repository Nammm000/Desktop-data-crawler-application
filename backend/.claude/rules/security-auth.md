# Security & Auth Rules

## Token model

- **Access token**: JWT (HS256), 15 min, `Authorization: Bearer <token>`. Claims:
  `sub` (user id), `role`, `type: "access"`, `iat`, `exp`. Decoded statelessly,
  but the user is re-fetched per request.
- **Refresh token**: opaque 384-bit random string (`secrets.token_urlsafe(48)`), 7 day
  lifetime, stored as SHA-256 hash only (`hash_refresh_token`). Travels in JSON body.
- Never put sensitive data in JWT claims; any new JWT type must carry a `type` claim.

## Invariants — must survive any refactor

- `passwordHash` never appears in any response schema. `UserOut.from_doc` is the only
  user→response boundary.
- Refresh rotation stays atomic: `find_one_and_update` with
  `{"tokenHash": h, "revokedAt": None, "expiresAt": {"$gt": now}}` in
  `app/services/token_service.py`. Concurrent refreshes with the same token must have
  exactly one winner.
- Replaying a revoked refresh token revokes ALL of that user's refresh tokens
  (family revocation). Unknown tokens must NOT cascade — random garbage must not
  log users out (DoS).
- `get_current_user` (`app/api/deps.py`) re-fetches the user from DB and rejects
  `status != "active"` with `403` — bans apply immediately, role comes from the DB
  doc, not the JWT claim.
- Login returns an identical generic `401 "Invalid email or password"` for unknown
  email and wrong password, and runs `dummy_password_check()` on unknown email
  (timing equalization — no account enumeration).
- Change password: verify current → update hash → revoke all refresh tokens →
  return exactly one fresh `TokenPair`.
- Passwords: bcrypt directly (`app/core/security.py`), never passlib. `verify_password`
  swallows `ValueError` and returns `False`. Max 64 chars / 72 bytes enforced in
  `app/schemas/auth.py`.
- Secrets: `JWT_SECRET` comes only from `.env` via pydantic-settings. Never log
  tokens, hashes, or passwords. 401 responses carry `WWW-Authenticate: Bearer`.
- Mongo URIs with auth must include `authSource=admin` (root user lives in `admin`).

## Adding auth to a new endpoint

Use the existing dependencies — never decode JWTs manually in a route:

- Any authenticated user: `user: CurrentUser`
- Admins only: `user: Annotated[dict, Depends(get_current_admin)]`
