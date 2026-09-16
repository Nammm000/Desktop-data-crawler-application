from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import CurrentUser, DbDep
from app.core.config import get_settings
from app.core.security import (
    create_access_token,
    dummy_password_check,
    hash_password,
    verify_password,
)
from app.models.user import UserStatus
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    RefreshTokenRequest,
    SignupRequest,
    TokenPair,
)
from app.schemas.user import UserOut
from app.services import token_service, user_service
from app.services.token_service import RefreshTokenError, RefreshTokenReuseError

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_pair(user: dict, refresh_token: str) -> TokenPair:
    settings = get_settings()
    return TokenPair(
        access_token=create_access_token(user_id=user["_id"], role=user["role"]),
        refresh_token=refresh_token,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserOut.from_doc(user),
    )


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def signup(data: SignupRequest, db: DbDep) -> UserOut:
    doc = await user_service.create_user(db, data)
    return UserOut.from_doc(doc)


@router.post("/login", response_model=TokenPair)
async def login(data: LoginRequest, db: DbDep) -> TokenPair:
    user = await user_service.get_by_email(db, data.email)
    if user is None:
        # Equalize timing with the password check that would have run.
        dummy_password_check(data.password)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if not verify_password(data.password, user["passwordHash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    if user["status"] != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is not active"
        )
    refresh = await token_service.issue_refresh_token(db, user["_id"])
    return _token_pair(user, refresh)


@router.post("/refresh", response_model=TokenPair)
async def refresh(data: RefreshTokenRequest, db: DbDep) -> TokenPair:
    try:
        new_refresh, user_id = await token_service.rotate_refresh_token(
            db, data.refresh_token
        )
    except RefreshTokenReuseError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token reuse detected; all sessions revoked",
        )
    except RefreshTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    # Build the access token from a fresh fetch so role/status changes apply.
    user = await user_service.get_by_id(db, user_id)
    if user is None:
        await token_service.revoke_all_for_user(db, user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    if user["status"] != UserStatus.ACTIVE:
        await token_service.revoke_all_for_user(db, user_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is not active"
        )
    return _token_pair(user, new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(data: LogoutRequest, db: DbDep) -> Response:
    await token_service.revoke_refresh_token(db, data.refresh_token)
    # Idempotent: an unknown token still returns 204 so nothing leaks.
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/change-password", response_model=TokenPair)
async def change_password(
    data: ChangePasswordRequest, user: CurrentUser, db: DbDep
) -> TokenPair:
    if not verify_password(data.current_password, user["passwordHash"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Current password is incorrect"
        )
    if data.current_password == data.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New password must be different from the current password",
        )

    await user_service.update_password(db, user["_id"], hash_password(data.new_password))
    # Kill every session; the current device gets a fresh pair in the response.
    await token_service.revoke_all_for_user(db, user["_id"])
    refresh = await token_service.issue_refresh_token(db, user["_id"])
    return _token_pair(user, refresh)
