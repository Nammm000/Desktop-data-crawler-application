"""WebSocket notification client (temporary feature).

Owns one QWebSocket to /api/v1/notifications/ws plus a 5 s reconnect timer.
QWebSocket is event-loop native, so everything runs on the GUI thread — no
worker threads, no QSettings. Unread/menu state lives in MainPage; this class
only turns frames into Notification objects.
"""

from __future__ import annotations

import json

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QAbstractSocket
from PySide6.QtWebSockets import QWebSocket

from app.api.client import Notification, parse_notification
from app.core.session import SessionController

_RECONNECT_DELAY_MS = 5_000


class NotificationClient(QObject):
    notification_received = Signal(object)  # Notification

    def __init__(self, session: SessionController, parent: QObject | None = None):
        super().__init__(parent)
        self._session = session
        self._active = False  # True between start() and stop()
        # True once the server accepted the CURRENT attempt; only _open() may
        # reset it (a clean drop after a completed handshake reconnects with
        # the same token, a dead handshake suggests a stale token instead).
        self._handshake_done = False
        self._last_token: str | None = None
        self._socket = QWebSocket()
        self._socket.connected.connect(self._on_connected)
        self._socket.textMessageReceived.connect(self._on_text_message)
        # A failed handshake may not emit disconnected(), so both signals land
        # in the same handler; a double fire just restarts the single-shot timer.
        self._socket.errorOccurred.connect(self._on_disconnected)
        self._socket.disconnected.connect(self._on_disconnected)
        self._reconnect_timer = QTimer(self)
        self._reconnect_timer.setSingleShot(True)
        self._reconnect_timer.setInterval(_RECONNECT_DELAY_MS)
        self._reconnect_timer.timeout.connect(self._reconnect)

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        self._active = True
        self._reconnect_timer.stop()
        self._open()

    def stop(self) -> None:
        self._active = False
        self._reconnect_timer.stop()
        self._socket.abort()  # abort(): drops even a pending handshake

    # -- internals ------------------------------------------------------------

    def _open(self) -> None:
        token = self._session.current_access_token()
        if token is None:
            return  # signed out; the next session_started calls start() again
        if self._socket.state() != QAbstractSocket.SocketState.UnconnectedState:
            self._socket.abort()
        self._handshake_done = False
        self._last_token = token
        self._socket.open(QUrl(self._session.client.websocket_url(token)))

    def _on_connected(self) -> None:
        self._handshake_done = True  # accept() implies auth passed

    def _on_text_message(self, message: str) -> None:
        try:
            data = json.loads(message)
        except ValueError:
            return
        notification = parse_notification(data)
        if notification is not None and notification.message:
            self.notification_received.emit(notification)

    def _on_disconnected(self) -> None:
        if self._active:
            self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        self._reconnect_timer.start()

    def _reconnect(self) -> None:
        if not self._active:
            return
        token = self._session.current_access_token()
        if self._handshake_done or token != self._last_token:
            # Clean drop, or another call already rotated the pair -> re-open.
            self._open()
            return
        # The last attempt died at the handshake with the same token -> likely
        # an expired access token. Single-flight refresh; a dead refresh token
        # force-logs-out -> session_ended -> stop().
        self._session.refresh_access_token(
            on_success=lambda _token: self._open(),
            on_error=lambda _exc: self._schedule_reconnect(),
        )
