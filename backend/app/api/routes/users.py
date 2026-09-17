from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import AdminUser, CurrentUser, DbDep
from app.models.user import UserStatus
from app.schemas.user import (
    UserBulkDeleteRequest,
    UserDeleteResult,
    UserList,
    UserOut,
    UserRoleUpdateRequest,
    UserStatusUpdateRequest,
)
from app.services import token_service, user_service

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def read_me(user: CurrentUser) -> UserOut:
    return UserOut.from_doc(user)


@router.get("", response_model=UserList)
async def list_users(
    db: DbDep,
    admin: AdminUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    skip: Annotated[int, Query(ge=0)] = 0,
) -> UserList:
    users, total = await user_service.list_users(db, skip=skip, limit=limit)
    return UserList(users=[UserOut.from_doc(u) for u in users], total=total)


@router.patch("/{user_id}/status", response_model=UserOut)
async def update_user_status(
    user_id: str, data: UserStatusUpdateRequest, admin: AdminUser, db: DbDep
) -> UserOut:
    if user_id == admin["_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot change their own status",
        )
    doc = await user_service.update_user_status(db, user_id, data.status)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    if data.status != UserStatus.ACTIVE:
        # Ban/deactivation applies immediately: kill every session. Re-activating
        # revokes nothing (the tokens were already burned at ban time).
        await token_service.revoke_all_for_user(db, user_id)
    return UserOut.from_doc(doc)


@router.patch("/{user_id}/role", response_model=UserOut)
async def update_user_role(
    user_id: str, data: UserRoleUpdateRequest, admin: AdminUser, db: DbDep
) -> UserOut:
    if user_id == admin["_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot change their own role",
        )
    doc = await user_service.update_user_role(db, user_id, data.role.value)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    return UserOut.from_doc(doc)


@router.delete("", response_model=UserDeleteResult)
async def delete_users(
    data: UserBulkDeleteRequest, admin: AdminUser, db: DbDep
) -> UserDeleteResult:
    if admin["_id"] in data.user_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot delete their own account",
        )
    deleted = await user_service.delete_users(db, data.user_ids)
    # Tokens of ALL requested ids are removed — also cleans up orphans left by
    # users that were already gone.
    await token_service.delete_for_users(db, data.user_ids)
    return UserDeleteResult(deleted=deleted)


@router.delete(
    "/{user_id}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_user(user_id: str, admin: AdminUser, db: DbDep) -> Response:
    if user_id == admin["_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admins cannot delete their own account",
        )
    if not await user_service.delete_user(db, user_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )
    await token_service.delete_for_users(db, [user_id])
    return Response(status_code=status.HTTP_204_NO_CONTENT)
