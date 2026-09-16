"""Synchronous HTTP client for the data-crawler backend.

Pure Python (no Qt) so it is safe to call from worker threads. All request
and response bodies use the backend's camelCase JSON convention; errors are
normalized into ApiError with a human-readable message.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

import requests

DEFAULT_BASE_URL = "http://localhost:8000"


class ApiError(Exception):
    """Backend or transport failure with a displayable message."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        # None marks transport failures (server unreachable, timeout, ...).
        self.status_code = status_code


@dataclass(frozen=True)
class User:
    id: str
    username: str
    email: str
    role: str
    status: str
    created_at: datetime | None


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    token_type: str
    expires_in: int
    user: User


def _parse_user(data: dict) -> User:
    created_at = None
    raw = data.get("createdAt")
    if raw:
        try:
            created_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            pass
    return User(
        id=data["id"],
        username=data["username"],
        email=data["email"],
        role=data.get("role", "user"),
        status=data.get("status", "active"),
        created_at=created_at,
    )


def _parse_token_pair(data: dict) -> TokenPair:
    return TokenPair(
        access_token=data["accessToken"],
        refresh_token=data["refreshToken"],
        token_type=data.get("tokenType", "bearer"),
        expires_in=int(data.get("expiresIn", 0)),
        user=_parse_user(data["user"]),
    )


def _parse_or_fail(payload, parser):
    try:
        return parser(payload)
    except (KeyError, TypeError, ValueError, IndexError):
        raise ApiError("Unexpected response from the server") from None


class ApiClient:
    """Thin wrapper around all backend endpoints."""

    def __init__(self, base_url: str | None = None, timeout: float = 10.0):
        self.base_url = (
            base_url
            or os.environ.get("DATA_CRAWLER_API_URL", DEFAULT_BASE_URL)
        ).rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    # -- plumbing ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        bearer: str | None = None,
    ) -> dict | None:
        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            response = self._session.request(
                method,
                f"{self.base_url}{path}",
                json=json_body,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException:
            raise ApiError(f"Could not reach the server at {self.base_url}") from None

        if response.status_code == 204 or not response.content:
            return None
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.ok:
            return payload
        raise ApiError(
            self._error_message(response.status_code, payload), response.status_code
        )

    @staticmethod
    def _error_message(status: int, payload) -> str:
        detail = payload.get("detail") if isinstance(payload, dict) else None
        if isinstance(detail, str) and detail:
            return detail
        if isinstance(detail, list):  # FastAPI 422 validation errors
            parts = []
            for item in detail:
                if isinstance(item, dict):
                    loc = item.get("loc") or []
                    field = str(loc[-1]) if loc else "request"
                    parts.append(f"{field}: {item.get('msg', 'invalid value')}")
                else:
                    parts.append(str(item))
            if parts:
                return "; ".join(parts)
        return f"Request failed (HTTP {status})"

    # -- endpoints --------------------------------------------------------

    def health(self) -> dict:
        return self._request("GET", "/api/health") or {}

    def signup(self, *, username: str, email: str, password: str) -> User:
        payload = self._request(
            "POST",
            "/api/v1/auth/signup",
            json_body={
                "username": username.strip().lower(),
                "email": email.strip().lower(),
                "password": password,
            },
        )
        return _parse_or_fail(payload, _parse_user)

    def login(self, *, email: str, password: str) -> TokenPair:
        payload = self._request(
            "POST",
            "/api/v1/auth/login",
            json_body={"email": email.strip().lower(), "password": password},
        )
        return _parse_or_fail(payload, _parse_token_pair)

    def refresh(self, *, refresh_token: str) -> TokenPair:
        payload = self._request(
            "POST",
            "/api/v1/auth/refresh",
            json_body={"refreshToken": refresh_token},
        )
        return _parse_or_fail(payload, _parse_token_pair)

    def logout(self, *, refresh_token: str) -> None:
        self._request(
            "POST", "/api/v1/auth/logout", json_body={"refreshToken": refresh_token}
        )

    def change_password(
        self, *, access_token: str, current_password: str, new_password: str
    ) -> TokenPair:
        payload = self._request(
            "POST",
            "/api/v1/auth/change-password",
            json_body={
                "currentPassword": current_password,
                "newPassword": new_password,
            },
            bearer=access_token,
        )
        return _parse_or_fail(payload, _parse_token_pair)

    def get_current_user(self, *, access_token: str) -> User:
        payload = self._request("GET", "/api/v1/users/me", bearer=access_token)
        return _parse_or_fail(payload, _parse_user)
