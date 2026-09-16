from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.security import decode_access_token
from app.db.mongo import get_db
from app.models.user import UserStatus, UserRole
from app.services import user_service

bearer_scheme = HTTPBearer(auto_error=False)

DbDep = Annotated[AsyncIOMotorDatabase, Depends(get_db)]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncIOMotorDatabase = Depends(get_db),
) -> dict:
    if credentials is None:
        raise _unauthorized("Not authenticated")

    try:
        claims = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise _unauthorized("Access token expired")
    except jwt.PyJWTError:
        raise _unauthorized("Invalid access token")

    if claims.get("type") != "access":
        raise _unauthorized("Invalid access token")

    # Re-fetch the user so bans/status changes and deletions take effect immediately.
    user = await user_service.get_by_id(db, claims.get("sub", ""))
    if user is None:
        raise _unauthorized("User not found")
    if user["status"] != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is not active"
        )
    return user


CurrentUser = Annotated[dict, Depends(get_current_user)]


async def get_current_admin(user: CurrentUser) -> dict:
    if user["role"] != UserRole.ADMIN.value:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Admin privileges required"
        )
    return user
