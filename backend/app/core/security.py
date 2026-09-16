import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import get_settings

# Lazily computed hash of a random string: used to equalize login timing when
# the email is unknown, so response time doesn't reveal whether an account exists.
_dummy_hash: str | None = None


def hash_password(password: str) -> str:
    settings = get_settings()
    return bcrypt.hashpw(
        password.encode(), bcrypt.gensalt(rounds=settings.bcrypt_rounds)
    ).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        # Invalid salt or password beyond bcrypt's 72-byte limit.
        return False


def dummy_password_check(password: str) -> None:
    """Burn the same CPU time as a real password check (anti account-enumeration)."""
    global _dummy_hash
    if _dummy_hash is None:
        settings = get_settings()
        _dummy_hash = bcrypt.hashpw(
            secrets.token_urlsafe(24).encode(),
            bcrypt.gensalt(rounds=settings.bcrypt_rounds),
        ).decode()
    try:
        bcrypt.checkpw(password.encode(), _dummy_hash.encode())
    except ValueError:
        pass


def create_access_token(*, user_id: str, role: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Decode and verify an access token.

    Raises jwt.ExpiredSignatureError for expired tokens, jwt.PyJWTError otherwise.
    """
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])


def generate_refresh_token() -> str:
    """Opaque 384-bit random token; only its SHA-256 hash is ever stored."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()
