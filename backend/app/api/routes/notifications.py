"""Authenticated notification stream: pushes a fixed message to each
connected client on a timer (default 15 min) and relays backend broadcasts
(agent crawl status) sent through the shared connection manager."""

import asyncio
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.deps import WsDbDep
from app.core.config import SettingsDep
from app.core.security import decode_access_token
from app.models.user import UserStatus
from app.services import user_service
from app.services.connection_manager import manager

router = APIRouter(prefix="/notifications", tags=["notifications"])

_CLOSE_POLICY_VIOLATION = 1008


async def _handshake_user(websocket: WebSocket, db) -> dict | None:
    """get_current_user's logic for the WS handshake (token via query param,
    because QWebSocket cannot set handshake headers). HTTPException is not
    usable in WS routes — None means 'reject with close code 1008'."""
    token = websocket.query_params.get("token", "")
    if not token:
        return None
    try:
        claims = decode_access_token(token)
    except jwt.PyJWTError:  # includes ExpiredSignatureError
        return None
    if claims.get("type") != "access":
        return None
    user = await user_service.get_by_id(db, claims.get("sub", ""))
    if user is None or user["status"] != UserStatus.ACTIVE:
        return None
    return user


@router.websocket("/ws")
async def notification_stream(
    websocket: WebSocket, db: WsDbDep, settings: SettingsDep
) -> None:
    user = await _handshake_user(websocket, db)
    if user is None:
        # close() before accept() surfaces to the client as an HTTP 403
        # handshake rejection.
        await websocket.close(code=_CLOSE_POLICY_VIOLATION)
        return
    await websocket.accept()
    await websocket.send_json({"type": "connected"})
    manager.connect(websocket)
    try:
        while True:
            # Race the interval sleep against an inbound frame so a client
            # that vanishes is reaped immediately instead of only when the
            # next (possibly 15-min-later) push fails.
            receive_task = asyncio.create_task(websocket.receive_text())
            sleep_task = asyncio.create_task(
                asyncio.sleep(settings.notification_interval_seconds)
            )
            await asyncio.wait(
                {receive_task, sleep_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if sleep_task.done():
                receive_task.cancel()
            else:
                sleep_task.cancel()
                if receive_task.exception() is not None:
                    # WebSocketDisconnect / broken socket — handled below.
                    raise receive_task.exception()
                # A client message: the protocol is server-push only, so
                # ignore it and keep pushing.
                continue
            minutes = settings.notification_interval_seconds // 60
            await websocket.send_json(
                {
                    "type": "notification",
                    "message": f"{minutes} minutes have passed",
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                }
            )
    except (WebSocketDisconnect, RuntimeError):
        # Client went away; a send on a dead socket also lands here.
        return
    finally:
        manager.disconnect(websocket)
