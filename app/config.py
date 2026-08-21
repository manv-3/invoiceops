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
    notion_api_version: str = "2026-03-11"
    notion_invoices_data_source_id: str | None = None
    notion_vendors_data_source_id: str | None = None
    notion_run_log_data_source_id: str | None = None

    gemini_api_key: SecretStr | None = None
    gemini_model: str | None = None

    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    telegram_api_base_url: str = "https://api.telegram.org"

    approval_poll_seconds: int = Field(default=10, ge=1)
    enable_approval_poller: bool = False
    max_upload_mb: int = Field(default=15, ge=1)
    storage_dir: Path = Path("storage")
    auto_process_clean_invoices: bool = True
    dev_mode: bool = True
    confidence_scale: Literal["0-1", "0-100"] = "0-1"


def configuration_errors(settings: Settings) -> list[str]:
    """Return actionable configuration errors for the selected runtime mode."""

    errors: list[str] = []
    notion_fields = {
        "NOTION_TOKEN": settings.notion_token,
        "NOTION_INVOICES_DATA_SOURCE_ID": settings.notion_invoices_data_source_id,
        "NOTION_VENDORS_DATA_SOURCE_ID": settings.notion_vendors_data_source_id,
        "NOTION_RUN_LOG_DATA_SOURCE_ID": settings.notion_run_log_data_source_id,
    }
    if settings.app_env == "production":
        errors.extend(name for name, value in notion_fields.items() if not value)
        if settings.auto_process_clean_invoices:
            if settings.gemini_api_key is None:
                errors.append("GEMINI_API_KEY")
            if not settings.gemini_model:
                errors.append("GEMINI_MODEL")
    if settings.enable_approval_poller:
        errors.extend(name for name, value in notion_fields.items() if not value)
    if settings.app_env == "production" and settings.telegram_bot_token:
        if settings.telegram_webhook_secret is None:
            errors.append("TELEGRAM_WEBHOOK_SECRET")
    return list(dict.fromkeys(errors))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide validated settings instance."""

    return Settings()
