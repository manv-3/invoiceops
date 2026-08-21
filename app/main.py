"""FastAPI application entrypoint for InvoiceOps."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.routes.health import router as health_router
from app.routes.invoices import router as invoices_router
from app.workers.approval_worker import run_approval_worker


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Prepare local storage and start background workers before serving requests."""

    settings = get_settings()
    for directory in (
        settings.storage_dir / "incoming",
        settings.storage_dir / "approved",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    # Start the approval worker as a background task.
    worker_task = asyncio.create_task(run_approval_worker())
    try:
        yield
    finally:
        worker_task.cancel()
        try:
            await worker_task
        except asyncio.CancelledError:
            pass


settings = get_settings()
app = FastAPI(
    title="InvoiceOps",
    version=settings.service_version,
    lifespan=lifespan,
)
app.include_router(health_router)
app.include_router(invoices_router)
