from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic.alias_generators import to_camel

from app.schemas.user import UserOut

# 8–64 characters. Length is also checked in bytes (bcrypt 5 raises for
# passwords longer than 72 bytes — multibyte UTF-8 can exceed that before 64 chars).
PASSWORD_FIELD = Field(min_length=8, max_length=64)

_BCRYPT_BYTE_LIMIT = 72


def _check_password_bytes(value: str) -> str:
    if len(value.encode("utf-8")) > _BCRYPT_BYTE_LIMIT:
        raise ValueError("Password must not exceed 72 bytes")
    return value


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class SignupRequest(_CamelModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_.-]+$")
    email: EmailStr
    password: str = PASSWORD_FIELD

    @field_validator("username", "email", mode="before")
    @classmethod
    def lowercase(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v

    @field_validator("password")
    @classmethod
    def password_bytes(cls, v: str) -> str:
        return _check_password_bytes(v)


class LoginRequest(_CamelModel):
    email: EmailStr
    password: str

    @field_validator("email", mode="before")
    @classmethod
    def lowercase_email(cls, v):
        if isinstance(v, str):
            return v.lower()
        return v


class RefreshTokenRequest(_CamelModel):
    refresh_token: str = Field(min_length=20)


class LogoutRequest(RefreshTokenRequest):
    pass


class ChangePasswordRequest(_CamelModel):
    current_password: str
    new_password: str = PASSWORD_FIELD

    @field_validator("new_password")
    @classmethod
    def password_bytes(cls, v: str) -> str:
        return _check_password_bytes(v)


class TokenPair(_CamelModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # access token lifetime in seconds
    user: UserOut
