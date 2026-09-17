---
paths:
  - "app/**/*.py"
---

# Coding Conventions

## Style

- Python 3.14, full type hints, `X | None` unions, async everywhere in request paths.
- Pydantic v2 idioms only: `ConfigDict`, `field_validator`, `alias_generator=to_camel`,
  `populate_by_name=True`. No `pydantic.v1` shim, no `@validator`.
- Docstrings on service functions and non-trivial public helpers; inline comments
  only for non-obvious decisions (the "why", not the "what").

## Layering

- `api/routes/`: HTTP concerns only — parse, authorize, call a service, map to a
  response schema. Business logic lives in `app/services/`.
- Services return plain dicts (Mongo documents) and raise `HTTPException` only for
  user-facing conflicts (e.g. 409); ownership of HTTP semantics otherwise stays
  in routes.
- Mapping to response models happens at the route boundary via `from_doc`
  classmethods (`UserOut.from_doc`) — this is where fields get filtered
  (e.g. `passwordHash` dropped) and `_id` becomes `id`.
- Shared request/response models go in `app/schemas/`; enums and collection-name
  constants in `app/models/`.

## Dependency injection

- Reuse the existing aliases instead of raw `Depends`:
  `DbDep` / `CurrentUser` / `AdminUser` (`app/api/deps.py`),
  `SettingsDep` (`app/core/config.py`).
- Settings: `get_settings()` is `@lru_cache`d — read config through it, never
  `os.getenv`, never instantiate `Settings()` elsewhere.

## Errors & time

- Raise `HTTPException` with the status-code semantics from `api-surface.md`.
- New error classes follow `token_service`'s pattern: specific exceptions
  (`RefreshTokenReuseError`) translated to HTTP responses in the route.
- Always tz-aware UTC datetimes.
