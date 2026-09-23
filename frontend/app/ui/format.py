"""Shared display formatting for user data (settings page, user management)."""

from __future__ import annotations

from datetime import datetime


def format_date(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d %b %Y")


def format_time(value: datetime | None) -> str:
    """Local-time HH:MM (notification menu entries)."""
    if value is None:
        return ""
    return value.astimezone().strftime("%H:%M")


def format_role(role: str) -> str:
    return role.capitalize() if role else "—"


def format_status(status: str) -> str:
    return status.capitalize() if status else "—"


# Human labels for crawl failure reasons (kept in sync with the backend's
# FailureReason constants — unknown future reasons fall back to the raw code).
_FAILURE_REASONS = {
    "broken_link": "Broken link (404)",
    "rate_limited": "Rate limited (429)",
    "http_error": "HTTP error",
    "login_redirect": "Login required (redirected)",
    "checkpoint": "Bot check / checkpoint",
    "cookie_missing": "Login cookies missing",
    "timeout": "Timed out",
    "dns_error": "Host not found (DNS)",
    "connection_error": "Connection failed",
    "proxy_error": "Proxy failed",
    "unexpected_html": "Page fetched but nothing matched",
    "cancelled": "Cancelled",
    "request_error": "Request failed",
}


def format_failure_reason(reason: str) -> str:
    return _FAILURE_REASONS.get(reason, reason.replace("_", " ").capitalize())
