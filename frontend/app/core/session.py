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
    AgentCredentialsMeta,
    AgentData,
    AgentDataPage,
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
    tokens_rotated = Signal(
        str, int
    )  # (new refresh token, session generation) -> persisted on the GUI thread

    def __init__(self, client: ApiClient, parent=None):
        super().__init__(parent)
        self._client = client
        self._settings = QSettings()
        # Guards single-flight refresh ONLY (held across the network call —
        # worker threads wait on it). Never taken by GUI-thread readers:
        # the token attributes are atomically swapped, never mutated.
        self._rotate_lock = threading.Lock()
        # Bumped by every logout/force-logout; a rotation that completes on a
        # worker thread afterwards belongs to a dead session and must never
        # reach QSettings.
        self._session_generation = 0
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self.user: User | None = None

        self.tokens_rotated.connect(self._persist_refresh_token)
        self._clear_tokens_requested.connect(self._clear_persisted_refresh_token)

    # Signals used to keep all QSettings I/O on the GUI thread.
    _clear_tokens_requested = Signal()

    # -- persistence (GUI thread only) --------------------------------------

    def _persist_refresh_token(self, refresh_token: str, generation: int) -> None:
        # Guard against the logout race: a rotation completing on a worker
        # thread right after logout() cleared state would re-queue this
        # write and resurrect the "logged out" session on next launch. The
        # generation is compared on the GUI thread (same thread as logout),
        # so the check cannot interleave with it.
        if generation != self._session_generation:
            return
        if refresh_token != self._refresh_token:
            return
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
        source_type: str = "generic",
        on_success: Callable[[Agent], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> Agent:
            return self._authorized_call(
                lambda token: self._client.create_agent(
                    access_token=token,
                    name=name,
                    format=format,
                    script=script,
                    source_type=source_type,
                )
            )

        run_async(work, on_success or (lambda _agent: None), on_error or (lambda _exc: None))

    def update_agent(
        self,
        agent_id: str,
        name: str,
        format: str,
        script: str,
        source_type: str = "generic",
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
                    source_type=source_type,
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

    def run_agent(
        self,
        agent_id: str,
        on_success: Callable[[Agent], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> Agent:
            return self._authorized_call(
                lambda token: self._client.run_agent(
                    access_token=token, agent_id=agent_id
                )
            )

        run_async(work, on_success or (lambda _agent: None), on_error or (lambda _exc: None))

    def stop_agent(
        self,
        agent_id: str,
        on_success: Callable[[Agent], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> Agent:
            return self._authorized_call(
                lambda token: self._client.stop_agent(
                    access_token=token, agent_id=agent_id
                )
            )

        run_async(work, on_success or (lambda _agent: None), on_error or (lambda _exc: None))

    def set_agent_credentials(
        self,
        agent_id: str,
        cookie_header: str | None = None,
        proxy_text: str | None = None,
        on_success: Callable[[Agent], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> Agent:
            return self._authorized_call(
                lambda token: self._client.set_agent_credentials(
                    access_token=token,
                    agent_id=agent_id,
                    cookie_header=cookie_header,
                    proxy_text=proxy_text,
                )
            )

        run_async(work, on_success or (lambda _agent: None), on_error or (lambda _exc: None))

    def get_agent_credentials_metadata(
        self,
        agent_id: str,
        on_success: Callable[[AgentCredentialsMeta], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> AgentCredentialsMeta:
            return self._authorized_call(
                lambda token: self._client.get_agent_credentials_metadata(
                    access_token=token, agent_id=agent_id
                )
            )

        run_async(work, on_success or (lambda _meta: None), on_error or (lambda _exc: None))

    def clear_agent_credentials(
        self,
        agent_id: str,
        on_success: Callable[[Agent], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> Agent:
            return self._authorized_call(
                lambda token: self._client.clear_agent_credentials(
                    access_token=token, agent_id=agent_id
                )
            )

        run_async(work, on_success or (lambda _agent: None), on_error or (lambda _exc: None))

    def list_agent_data(
        self,
        agent_id: str,
        limit: int,
        skip: int,
        on_success: Callable[[AgentDataPage], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> AgentDataPage:
            return self._authorized_call(
                lambda token: self._client.list_agent_data(
                    access_token=token, agent_id=agent_id, limit=limit, skip=skip
                )
            )

        run_async(work, on_success or (lambda _page: None), on_error or (lambda _exc: None))

    def list_orphaned_data(
        self,
        limit: int,
        skip: int,
        on_success: Callable[[AgentDataPage], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> AgentDataPage:
            return self._authorized_call(
                lambda token: self._client.list_orphaned_data(
                    access_token=token, limit=limit, skip=skip
                )
            )

        run_async(work, on_success or (lambda _page: None), on_error or (lambda _exc: None))

    def delete_data(
        self,
        data_id: str,
        on_success: Callable[[None], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> None:
            return self._authorized_call(
                lambda token: self._client.delete_data(
                    access_token=token, data_id=data_id
                )
            )

        run_async(work, on_success or (lambda _none: None), on_error or (lambda _exc: None))

    def delete_data_items(
        self,
        data_ids: list[str],
        on_success: Callable[[int], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        def work() -> int:
            return self._authorized_call(
                lambda token: self._client.delete_data_items(
                    access_token=token, data_ids=data_ids
                )
            )

        run_async(work, on_success or (lambda _count: None), on_error or (lambda _exc: None))

    # -- notification stream ---------------------------------------------------

    @property
    def client(self) -> ApiClient:
        """Read-only handle for URL derivation (websocket_url); no requests."""
        return self._client

    def current_access_token(self) -> str | None:
        """Lock-free read of the in-memory access token (None while signed
        out). Called on the GUI thread (WebSocket reconnect path) — must
        never wait on the refresh lock, which a worker can hold across the
        whole (up to 10 s) network refresh."""
        return self._access_token

    def refresh_access_token(
        self,
        on_success: Callable[[str], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        """Force one single-flight token rotation (WebSocket reconnect path).
        A 401 refresh failure force-logs-out (dead refresh token); transport
        failures do not — the reconnect timer just retries later."""

        def work() -> str:
            try:
                return self._rotate_tokens()
            except ApiError as exc:
                if exc.status_code == 401:
                    self._force_logout()
                raise

        run_async(work, on_success or (lambda _token: None), on_error or (lambda _exc: None))

    # -- token plumbing (worker threads) ------------------------------------

    def _apply_pair(self, pair: TokenPair, generation: int | None = None) -> None:
        # GUI-thread callers may omit the generation (logout cannot interleave
        # there); worker callers pass the generation captured before their
        # network call so a logout mid-flight discards the result wholesale.
        if generation is not None and generation != self._session_generation:
            return
        self._access_token = pair.access_token
        self._refresh_token = pair.refresh_token
        self.user = pair.user
        self.tokens_rotated.emit(pair.refresh_token, self._session_generation)

    def _rotate_tokens(self, stale_access: str | None = None) -> str:
        """Single-flight refresh; returns a valid access token. Worker threads only.

        Callers that hit a 401 pass the token that failed: if another call has
        already rotated the pair meanwhile, we reuse its result instead of
        replaying the old refresh token (replay would revoke the whole family).
        """
        with self._rotate_lock:
            generation = self._session_generation
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
            self._apply_pair(pair, generation)
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
        # A manual logout may have won the race against this in-flight 401 —
        # the user is already on the login page, and a second session_ended
        # would show a misleading "session expired" message.
        if self._refresh_token is None and self.user is None:
            return
        self._clear_session_state()
        self.session_ended.emit(SESSION_EXPIRED_MESSAGE)

    def _clear_session_state(self) -> None:
        # Retires any rotation still in flight on a worker thread.
        self._session_generation += 1
        self._access_token = None
        self._refresh_token = None
        self.user = None
        self._clear_tokens_requested.emit()
