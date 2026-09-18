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


@dataclass(frozen=True)
class UserPage:
    users: tuple[User, ...]
    total: int


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    type: str  # "one_post"
    status: str  # "New"
    format: str  # "json" | "xml" | "md"
    script: str
    created_at: datetime | None
    updated_at: datetime | None
    updated_by: str


@dataclass(frozen=True)
class AgentPage:
    agents: tuple[Agent, ...]
    total: int


def _parse_timestamp(raw) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_user(data: dict) -> User:
    return User(
        id=data["id"],
        username=data["username"],
        email=data["email"],
        role=data.get("role", "user"),
        status=data.get("status", "active"),
        created_at=_parse_timestamp(data.get("createdAt")),
    )


def _parse_token_pair(data: dict) -> TokenPair:
    return TokenPair(
        access_token=data["accessToken"],
        refresh_token=data["refreshToken"],
        token_type=data.get("tokenType", "bearer"),
        expires_in=int(data.get("expiresIn", 0)),
        user=_parse_user(data["user"]),
    )


def _parse_user_page(data: dict) -> UserPage:
    return UserPage(
        users=tuple(_parse_user(u) for u in data["users"]),
        total=int(data["total"]),
    )


def _parse_agent(data: dict) -> Agent:
    return Agent(
        id=data["id"],
        name=data["name"],
        type=data.get("type", "one_post"),
        status=data.get("status", "New"),
        format=data.get("format", "json"),
        script=data.get("script", ""),
        created_at=_parse_timestamp(data.get("createdAt")),
        updated_at=_parse_timestamp(data.get("updatedAt")),
        updated_by=data.get("updatedBy", ""),
    )


def _parse_agent_page(data: dict) -> AgentPage:
    return AgentPage(
        agents=tuple(_parse_agent(a) for a in data["agents"]),
        total=int(data["total"]),
    )


def _parse_deleted_count(data: dict) -> int:
    return int(data["deleted"])


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
        params: dict | None = None,
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
                params=params,
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

    def list_users(
        self, *, access_token: str, limit: int = 50, skip: int = 0
    ) -> UserPage:
        payload = self._request(
            "GET",
            "/api/v1/users",
            params={"limit": limit, "skip": skip},
            bearer=access_token,
        )
        return _parse_or_fail(payload, _parse_user_page)

    def update_user_status(
        self, *, access_token: str, user_id: str, status: str
    ) -> User:
        payload = self._request(
            "PATCH",
            f"/api/v1/users/{user_id}/status",
            json_body={"status": status},
            bearer=access_token,
        )
        return _parse_or_fail(payload, _parse_user)

    def update_user_role(self, *, access_token: str, user_id: str, role: str) -> User:
        payload = self._request(
            "PATCH",
            f"/api/v1/users/{user_id}/role",
            json_body={"role": role},
            bearer=access_token,
        )
        return _parse_or_fail(payload, _parse_user)

    def delete_user(self, *, access_token: str, user_id: str) -> None:
        # 204 with an empty body: _request returns None, nothing to parse.
        self._request("DELETE", f"/api/v1/users/{user_id}", bearer=access_token)

    def delete_users(self, *, access_token: str, user_ids: list[str]) -> int:
        payload = self._request(
            "DELETE",
            "/api/v1/users",
            json_body={"userIds": list(user_ids)},
            bearer=access_token,
        )
        return _parse_or_fail(payload, _parse_deleted_count)

    # -- agents -----------------------------------------------------------

    def list_agents(
        self, *, access_token: str, limit: int = 50, skip: int = 0
    ) -> AgentPage:
        payload = self._request(
            "GET",
            "/api/v1/agents",
            params={"limit": limit, "skip": skip},
            bearer=access_token,
        )
        return _parse_or_fail(payload, _parse_agent_page)

    def create_agent(
        self, *, access_token: str, name: str, format: str, script: str
    ) -> Agent:
        payload = self._request(
            "POST",
            "/api/v1/agents",
            json_body={"name": name.strip(), "format": format, "script": script},
            bearer=access_token,
        )
        return _parse_or_fail(payload, _parse_agent)

    def update_agent(
        self,
        *,
        access_token: str,
        agent_id: str,
        name: str | None = None,
        format: str | None = None,
        script: str | None = None,
    ) -> Agent:
        body: dict = {}
        if name is not None:
            body["name"] = name.strip()
        if format is not None:
            body["format"] = format
        if script is not None:
            body["script"] = script
        payload = self._request(
            "PATCH", f"/api/v1/agents/{agent_id}", json_body=body, bearer=access_token
        )
        return _parse_or_fail(payload, _parse_agent)

    def delete_agent(self, *, access_token: str, agent_id: str) -> None:
        # 204 with an empty body: _request returns None, nothing to parse.
        self._request("DELETE", f"/api/v1/agents/{agent_id}", bearer=access_token)
