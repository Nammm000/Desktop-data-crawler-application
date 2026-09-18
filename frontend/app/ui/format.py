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
