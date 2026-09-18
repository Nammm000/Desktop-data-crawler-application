"""Authentication/session state controller.

Owns the token pair, persists the refresh token via QSettings (GUI thread
only), performs single-flight refreshes (the backend rotates both tokens on
every refresh and revokes the whole family on replay), and exposes GUI-thread
entry points that run their blocking work through app.core.worker.run_async.
"""

from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QObject, QSettings, Signal

from app.api.client import (
    Agent,
    AgentPage,
    ApiClient,
    ApiError,
    TokenPair,
    User,
    UserPage,
)
from app.core.worker import run_async

_REFRESH_TOKEN_KEY = "auth/refreshToken"
SESSION_EXPIRED_MESSAGE = "Your session expired. Please sign in again."


class SessionController(QObject):
    session_started = Signal(object)  # User
    session_ended = Signal(str)  # "" on manual logout, message otherwise
    auth_failed = Signal(str)  # login/signup error message
    backend_warning = Signal(str)  # non-fatal connectivity / database warning
    tokens_rotated = Signal(str)  # new refresh token -> persisted on the GUI thread

    def __init__(self, client: ApiClient, parent=None):
        super().__init__(parent)
        self._client = client
        self._settings = QSettings()
        self._lock = threading.Lock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self.user: User | None = None

        self.tokens_rotated.connect(self._persist_refresh_token)
        self._clear_tokens_requested.connect(self._clear_persisted_refresh_token)

    # Signals used to keep all QSettings I/O on the GUI thread.
    _clear_tokens_requested = Signal()

    # -- persistence (GUI thread only) --------------------------------------

    def _persist_refresh_token(self, refresh_token: str) -> None:
        self._settings.setValue(_REFRESH_TOKEN_KEY, refresh_token)

    def _clear_persisted_refresh_token(self) -> None:
        self._settings.remove(_REFRESH_TOKEN_KEY)

    def _stored_refresh_token(self) -> str | None:
        return self._settings.value(_REFRESH_TOKEN_KEY, "", type=str) or None

    # -- startup ------------------------------------------------------------

    def bootstrap(self) -> None:
        """Silently restore the session from the stored refresh token, if any."""
        run_async(
            self._client.health,
            self._on_health,
            lambda _exc: self.backend_warning.emit(
                f"Could not reach the API server at {self._client.base_url}."
            ),
        )
        stored = self._stored_refresh_token()
        if not stored:
            self.session_ended.emit("")
            return
        self._refresh_token = stored
        run_async(
            self._bootstrap_worker,
            self._on_bootstrap_success,
            self._on_bootstrap_failure,
        )

    def _bootstrap_worker(self) -> User:
        self._rotate_tokens()
        return self._authorized_call(
            lambda token: self._client.get_current_user(access_token=token)
        )

    def _on_health(self, payload: dict) -> None:
        if not isinstance(payload, dict) or payload.get("database") != "up":
            self.backend_warning.emit(
                "The API server is up, but its database is down — sign-in may fail."
            )

    def _on_bootstrap_success(self, user: User) -> None:
        self.user = user
        self.session_started.emit(user)

    def _on_bootstrap_failure(self, exc: Exception) -> None:
        self._clear_session_state()
        if isinstance(exc, ApiError) and exc.status_code is None:
            # Server unreachable: the health-check warning already explains it.
            self.session_ended.emit("")
        else:
            self.session_ended.emit(str(exc) or SESSION_EXPIRED_MESSAGE)

    # -- login / signup -----------------------------------------------------

    def login_and_start(self, email: str, password: str) -> None:
        run_async(
            lambda: self._client.login(email=email, password=password),
            self._on_pair_acquired,
            self._on_auth_failure,
        )

    def signup_and_start(self, username: str, email: str, password: str) -> None:
        def work() -> TokenPair:
            self._client.signup(username=username, email=email, password=password)
            # Signup returns the user but no tokens; sign in right away.
            return self._client.login(email=email, password=password)

        run_async(work, self._on_pair_acquired, self._on_auth_failure)

    def _on_pair_acquired(self, pair: TokenPair) -> None:
        self._apply_pair(pair)
        self.session_started.emit(pair.user)

    def _on_auth_failure(self, exc: Exception) -> None:
        self.auth_failed.emit(str(exc))

    # -- logout ---------------------------------------------------------------

    def logout(self) -> None:
        refresh_token = self._refresh_token
        self._clear_session_state()

        def work() -> None:
            if refresh_token:
                try:
                    self._client.logout(refresh_token=refresh_token)
                except ApiError:
                    pass  # Best-effort: the token expires server-side anyway.

        run_async(work, lambda _result: None, lambda _exc: None)
        self.session_ended.emit("")

    # -- authenticated calls --------------------------------------------------

    def fetch_current_user(
        self,
        on_success: Callable[[User], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> User:
            return self._authorized_call(
                lambda token: self._client.get_current_user(access_token=token)
            )

        def success(user: User) -> None:
            self.user = user
            if on_success:
                on_success(user)

        run_async(work, success, on_error or (lambda _exc: None))

    def change_password(
        self,
        current_password: str,
        new_password: str,
        on_success: Callable[[TokenPair], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Change the password. The backend revokes every session and returns
        a fresh token pair for this device, which we adopt here."""

        def work() -> TokenPair:
            return self._authorized_call(
                lambda token: self._client.change_password(
                    access_token=token,
                    current_password=current_password,
                    new_password=new_password,
                )
            )

        def success(pair: TokenPair) -> None:
            self._apply_pair(pair)
            if on_success:
                on_success(pair)

        run_async(work, success, on_error or (lambda _exc: None))

    def list_users(
        self,
        limit: int,
        skip: int,
        on_success: Callable[[UserPage], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> UserPage:
            return self._authorized_call(
                lambda token: self._client.list_users(
                    access_token=token, limit=limit, skip=skip
                )
            )

        run_async(work, on_success or (lambda _page: None), on_error or (lambda _exc: None))

    def update_user_status(
        self,
        user_id: str,
        status: str,
        on_success: Callable[[User], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> User:
            return self._authorized_call(
                lambda token: self._client.update_user_status(
                    access_token=token, user_id=user_id, status=status
                )
            )

        run_async(work, on_success or (lambda _user: None), on_error or (lambda _exc: None))

    def update_user_role(
        self,
        user_id: str,
        role: str,
        on_success: Callable[[User], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> User:
            return self._authorized_call(
                lambda token: self._client.update_user_role(
                    access_token=token, user_id=user_id, role=role
                )
            )

        run_async(work, on_success or (lambda _user: None), on_error or (lambda _exc: None))

    def delete_user(
        self,
        user_id: str,
        on_success: Callable[[None], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> None:
            return self._authorized_call(
                lambda token: self._client.delete_user(access_token=token, user_id=user_id)
            )

        run_async(work, on_success or (lambda _none: None), on_error or (lambda _exc: None))

    def delete_users(
        self,
        user_ids: list[str],
        on_success: Callable[[int], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> int:
            return self._authorized_call(
                lambda token: self._client.delete_users(
                    access_token=token, user_ids=user_ids
                )
            )

        run_async(work, on_success or (lambda _count: None), on_error or (lambda _exc: None))

    def list_agents(
        self,
        limit: int,
        skip: int,
        on_success: Callable[[AgentPage], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> AgentPage:
            return self._authorized_call(
                lambda token: self._client.list_agents(
                    access_token=token, limit=limit, skip=skip
                )
            )

        run_async(work, on_success or (lambda _page: None), on_error or (lambda _exc: None))

    def create_agent(
        self,
        name: str,
        format: str,
        script: str,
        on_success: Callable[[Agent], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> Agent:
            return self._authorized_call(
                lambda token: self._client.create_agent(
                    access_token=token, name=name, format=format, script=script
                )
            )

        run_async(work, on_success or (lambda _agent: None), on_error or (lambda _exc: None))

    def update_agent(
        self,
        agent_id: str,
        name: str,
        format: str,
        script: str,
        on_success: Callable[[Agent], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> Agent:
            return self._authorized_call(
                lambda token: self._client.update_agent(
                    access_token=token,
                    agent_id=agent_id,
                    name=name,
                    format=format,
                    script=script,
                )
            )

        run_async(work, on_success or (lambda _agent: None), on_error or (lambda _exc: None))

    def delete_agent(
        self,
        agent_id: str,
        on_success: Callable[[None], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> None:
            return self._authorized_call(
                lambda token: self._client.delete_agent(
                    access_token=token, agent_id=agent_id
                )
            )

        run_async(work, on_success or (lambda _none: None), on_error or (lambda _exc: None))

    # -- token plumbing (worker threads) ------------------------------------

    def _apply_pair(self, pair: TokenPair) -> None:
        self._access_token = pair.access_token
        self._refresh_token = pair.refresh_token
        self.user = pair.user
        self.tokens_rotated.emit(pair.refresh_token)

    def _rotate_tokens(self, stale_access: str | None = None) -> str:
        """Single-flight refresh; returns a valid access token. Worker threads only.

        Callers that hit a 401 pass the token that failed: if another call has
        already rotated the pair meanwhile, we reuse its result instead of
        replaying the old refresh token (replay would revoke the whole family).
        """
        with self._lock:
            if (
                stale_access is not None
                and self._access_token
                and self._access_token != stale_access
            ):
                return self._access_token
            refresh_token = self._refresh_token
            if not refresh_token:
                raise ApiError(SESSION_EXPIRED_MESSAGE, 401)
            pair = self._client.refresh(refresh_token=refresh_token)
            self._apply_pair(pair)
            return self._access_token

    def _authorized_call(self, fn: Callable[[str], object]) -> object:
        """Call fn(access_token), refreshing once and retrying on 401. Worker threads only."""
        access_token = self._access_token
        try:
            return fn(access_token)
        except ApiError as exc:
            if exc.status_code != 401 or not self._refresh_token:
                raise
        try:
            access_token = self._rotate_tokens(stale_access=access_token)
        except ApiError:
            self._force_logout()
            raise
        try:
            return fn(access_token)
        except ApiError as exc:
            if exc.status_code == 401:
                self._force_logout()
            raise

    def _force_logout(self) -> None:
        self._clear_session_state()
        self.session_ended.emit(SESSION_EXPIRED_MESSAGE)

    def _clear_session_state(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self.user = None
        self._clear_tokens_requested.emit()
