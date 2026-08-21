"""FastAPI application entrypoint for InvoiceOps."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import configuration_errors, get_settings
from app.routes.health import router as health_router
from app.routes.invoices import router as invoices_router
from app.services.action_service import ApprovalProcessor
from app.services.notion import NotionClient
from app.workers.approval_poller import ApprovalPoller


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Prepare local storage before serving requests."""

    settings = getattr(app.state, "settings", get_settings())
    errors = configuration_errors(settings)
    if errors:
        raise RuntimeError("Invalid InvoiceOps configuration: " + ", ".join(errors))
    for directory in (
        settings.storage_dir / "incoming",
        settings.storage_dir / "approved",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    poller_task: asyncio.Task[None] | None = None
    stop_event: asyncio.Event | None = None
    if (
        settings.enable_approval_poller
        and settings.notion_token is not None
        and settings.notion_invoices_data_source_id
        and settings.notion_vendors_data_source_id
        and settings.notion_run_log_data_source_id
    ):
        notion = NotionClient(
            token=settings.notion_token.get_secret_value(),
            invoices_data_source_id=settings.notion_invoices_data_source_id,
            vendors_data_source_id=settings.notion_vendors_data_source_id,
            run_log_data_source_id=settings.notion_run_log_data_source_id,
            api_version=settings.notion_api_version,
        )
        app.state.notion_client = notion
        app.state.approval_processor = ApprovalProcessor(
            notion, settings.storage_dir / "approved", settings.service_version
        )
        stop_event = asyncio.Event()
        approval_poller = ApprovalPoller(
            notion,
            app.state.approval_processor,
            settings.storage_dir / "approved",
            settings.approval_poll_seconds,
        )
        poller_task = asyncio.create_task(approval_poller.run_forever(stop_event))
    try:
        yield
    finally:
        if stop_event is not None:
            stop_event.set()
        if poller_task is not None:
            await poller_task
        notion = getattr(app.state, "notion_client", None)
        if isinstance(notion, NotionClient):
            await notion.close()


settings = get_settings()
app = FastAPI(
    title="InvoiceOps",
    version=settings.service_version,
    lifespan=lifespan,
)
app.state.settings = settings
app.include_router(health_router)
app.include_router(invoices_router)
