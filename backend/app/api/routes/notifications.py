"""Temporary notification stream: pushes a fixed message to each connected
client on a timer (default 15 min) over an authenticated WebSocket."""

import asyncio
from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.deps import WsDbDep
from app.core.config import SettingsDep
from app.core.security import decode_access_token
from app.models.user import UserStatus
from app.services import user_service

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
    try:
        while True:
            await asyncio.sleep(settings.notification_interval_seconds)
            await websocket.send_json(
                {
                    "type": "notification",
                    "message": "15 minutes have passed",
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                }
            )
    except (WebSocketDisconnect, RuntimeError):
        # Client went away; a send on a dead socket also lands here.
        return
