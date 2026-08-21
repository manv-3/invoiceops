"""Application configuration loaded from environment variables."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the InvoiceOps service.

    Provider credentials remain optional during the foundation phase. Provider-specific
    services will validate the credentials they need when those integrations are used.
    """

    model_config = SettingsConfigDict(
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: Literal["development", "test", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = Field(default=8000, ge=1, le=65535)
    service_version: str = "0.1.0"

    notion_token: SecretStr | None = None
    notion_invoices_data_source_id: str | None = None
    notion_vendors_data_source_id: str | None = None
    notion_run_log_data_source_id: str | None = None

    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None

    approval_poll_seconds: int = Field(default=10, ge=1)
    max_upload_mb: int = Field(default=15, ge=1)
    storage_dir: Path = Path("storage")
    auto_process_clean_invoices: bool = True
    dev_mode: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
