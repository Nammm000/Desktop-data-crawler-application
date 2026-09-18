"""Registry of live notification WebSockets so any service can broadcast
frames. The notifications route registers each accepted socket here; other
modules (e.g. crawler_service) push through `manager.broadcast`. Lives in
services/ because routes import services, never the other way around."""

import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """Tracks authenticated WS clients. Single event loop, so plain set
    mutations are safe. Dead sockets are pruned when a broadcast send fails
    (without a concurrent receive(), that is the only reaping opportunity —
    same limitation as the periodic push loop)."""

    def __init__(self) -> None:
        self._connections: set[WebSocket] = set()

    def connect(self, websocket: WebSocket) -> None:
        self._connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)

    @property
    def active(self) -> int:
        return len(self._connections)

    async def broadcast(self, message: dict) -> None:
        """Send one camelCase frame to every connected client; a failing
        socket is dropped without aborting the loop for the others."""
        for websocket in list(self._connections):  # snapshot: mutation-safe
            try:
                await websocket.send_json(message)
            except Exception:  # client gone / socket closed
                self._connections.discard(websocket)


manager = ConnectionManager()
