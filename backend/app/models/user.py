from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    USER = "user"


class UserStatus:
    """The user `status` field is a plain string (per spec). Only ACTIVE may log in."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    BANNED = "banned"


USERS_COLLECTION = "users"
REFRESH_TOKENS_COLLECTION = "refresh_tokens"
