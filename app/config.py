"""Freedom GP$ API — settings.

All configuration comes from environment variables (see .env.example).
No secrets are stored in code.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from the environment."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_name: str = "Freedom GP$ API"
    app_version: str = "1.0"
    app_description: str = "Financial Intelligence Operating System"
    debug: bool = False

    # Database (async PostgreSQL via SQLAlchemy)
    database_url: str = (
        "postgresql+asyncpg://freedom:freedom@localhost:5432/freedom_gps"
    )

    # Redis (cache / Celery broker)
    redis_url: str = "redis://localhost:6379/0"

    # JWT authentication
    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60 * 24  # 1 day

    # CORS (comma-separated origins, "*" for local dev only)
    cors_origins: str = "*"


settings = Settings()
