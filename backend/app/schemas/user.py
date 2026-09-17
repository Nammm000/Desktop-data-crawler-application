from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pydantic.alias_generators import to_camel

from app.models.user import UserStatus, UserRole


class UserOut(BaseModel):
    """Public view of a user. Never exposes passwordHash."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: str
    username: str
    email: EmailStr
    role: UserRole
    status: str
    created_at: datetime

    @classmethod
    def from_doc(cls, doc: dict) -> "UserOut":
        return cls(
            id=str(doc["_id"]),
            username=doc["username"],
            email=doc["email"],
            role=doc["role"],
            status=doc["status"],
            created_at=doc["createdAt"],
        )


# Wire values for `status`, derived from the UserStatus constants so the API
# contract and the stored values cannot drift.
UserStatusValue = Literal[UserStatus.ACTIVE, UserStatus.INACTIVE, UserStatus.BANNED]


class UserStatusUpdateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    status: UserStatusValue


class UserRoleUpdateRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    role: UserRole


class UserList(BaseModel):
    """Paginated admin listing of users."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    users: list[UserOut]
    total: int


class UserBulkDeleteRequest(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    user_ids: list[str] = Field(min_length=1)


class UserDeleteResult(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    deleted: int
