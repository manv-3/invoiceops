from app.config import Settings, configuration_errors


def test_production_configuration_requires_core_provider_settings() -> None:
    settings = Settings(_env_file=None, app_env="production")

    errors = configuration_errors(settings)

    assert "NOTION_TOKEN" in errors
    assert "NOTION_INVOICES_DATA_SOURCE_ID" in errors
    assert "GEMINI_API_KEY" in errors
    assert "GEMINI_MODEL" in errors


def test_development_configuration_can_start_without_optional_providers() -> None:
    settings = Settings(_env_file=None, app_env="development", auto_process_clean_invoices=False)

    assert configuration_errors(settings) == []


def test_enabled_poller_requires_all_notion_data_sources() -> None:
    settings = Settings(_env_file=None, enable_approval_poller=True)

    errors = configuration_errors(settings)

    assert errors == [
        "NOTION_TOKEN",
        "NOTION_INVOICES_DATA_SOURCE_ID",
        "NOTION_VENDORS_DATA_SOURCE_ID",
        "NOTION_RUN_LOG_DATA_SOURCE_ID",
    ]
