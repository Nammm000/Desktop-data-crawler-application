from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr
from pydantic.alias_generators import to_camel

from app.models.user import UserRole


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
