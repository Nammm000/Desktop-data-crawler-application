"""Cookie-header parsing for the agent dialog's paste preview.

Mirrors the backend's `agent_secret_service.parse_cookie_header` (same
tolerance: `a=1; b=2` and newline-separated pairs, quotes stripped, last
duplicate wins) so what the preview says is what the backend will store."""

from __future__ import annotations

_LOGIN_COOKIE_NAMES = {"c_user", "xs"}  # a logged-in session needs both


def parse_cookie_header(raw: str) -> dict[str, str]:
    cookies: dict[str, str] = {}
    for chunk in raw.replace("\n", ";").split(";"):
        name, sep, value = chunk.strip().partition("=")
        if not sep:
            continue
        name = name.strip()
        value = value.strip().strip('"')
        if name and value:
            cookies[name] = value
    return cookies


def cookie_preview(raw: str) -> str:
    """Human summary for the dialog: count, login-cookie presence, or the
    empty-state hint. Never renders cookie VALUES."""
    raw = raw or ""
    if not raw.strip():
        return ""
    cookies = parse_cookie_header(raw)
    if not cookies:
        return "No valid name=value pairs yet"
    names = set(cookies)
    login = sorted(names & _LOGIN_COOKIE_NAMES)
    summary = f"{len(cookies)} cookie{'s' if len(cookies) != 1 else ''} detected"
    if len(login) == len(_LOGIN_COOKIE_NAMES):
        summary += " — includes c_user and xs (logged-in session)"
    elif login:
        missing = ", ".join(sorted(_LOGIN_COOKIE_NAMES - names))
        summary += f" — includes {', '.join(login)} (a login also needs {missing})"
    return summary
