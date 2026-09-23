from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    mongodb_uri: str
    mongodb_db: str = "data_crawler"
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7
    bcrypt_rounds: int = 12
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    notification_interval_seconds: int = 900
    crawl_timeout_seconds: int = 600  # CLOSESPIDER_TIMEOUT: hard ceiling per run
    crawl_max_pages: int = 200  # CLOSESPIDER_PAGECOUNT: page cap per run
    # Fernet key encrypting agent cookies/proxies at rest (agent_secrets).
    # Blank or the placeholder disables storing credentials; generate with:
    #   python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
    credentials_encryption_key: str = ""
    # Facebook crawl pacing — deliberately gentle (one request at a time,
    # delayed + randomized) to keep the account away from checkpoints.
    facebook_download_delay: float = 3.0
    facebook_host: str = "mbasic.facebook.com"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


SettingsDep = Annotated[Settings, Depends(get_settings)]
