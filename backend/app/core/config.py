from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    app_name: str = "Bank Statement Converter API"
    api_prefix: str = "/api"
    session_cookie_name: str = "bsc_session"
    session_ttl_seconds: int = 1800
    session_cookie_max_age: int = 1800
    cors_origins: List[str] = Field(
        default_factory=lambda: [
            "http://localhost:4200",
            "http://localhost:4000",
            "http://127.0.0.1:4000",
        ]
    )
    secret_key: str = "change-me-in-production"
    max_upload_size_bytes: int = 10 * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    return Settings()
