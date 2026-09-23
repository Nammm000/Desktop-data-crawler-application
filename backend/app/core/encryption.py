"""Fernet encryption for stored agent credentials (cookies / proxies).

The key comes from Settings (CREDENTIALS_ENCRYPTION_KEY). A blank/placeholder
key disables credential storage: `get_cipher()` raises CredentialsKeyError,
which the secrets service surfaces as a 503-style HTTP error — the API never
silently stores plaintext."""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings

_PLACEHOLDER_VALUES = {"", "CHANGE_ME"}


class CredentialsKeyError(RuntimeError):
    """The credentials encryption key is missing or invalid."""


@lru_cache
def _cipher() -> Fernet:
    key = get_settings().credentials_encryption_key
    if key.strip() in _PLACEHOLDER_VALUES:
        raise CredentialsKeyError(
            "CREDENTIALS_ENCRYPTION_KEY is not configured — storing agent "
            "credentials is disabled. Generate one with: python3 -c "
            '"import base64,secrets; print(base64.urlsafe_b64encode('
            'secrets.token_bytes(32)).decode())"'
        )
    try:
        return Fernet(key.strip().encode())
    except (ValueError, TypeError) as exc:
        raise CredentialsKeyError(
            "CREDENTIALS_ENCRYPTION_KEY is not a valid Fernet key"
        ) from exc


def encrypt(plaintext: str) -> str:
    return _cipher().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CredentialsKeyError(
            "Stored credentials could not be decrypted — the "
            "CREDENTIALS_ENCRYPTION_KEY has changed; re-enter the agent's "
            "cookies/proxies"
        ) from exc
