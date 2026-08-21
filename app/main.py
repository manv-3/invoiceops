"""FastAPI application entrypoint for InvoiceOps."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.routes.health import router as health_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Prepare local storage before serving requests."""

    settings = get_settings()
    for directory in (
        settings.storage_dir / "incoming",
        settings.storage_dir / "approved",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    yield


settings = get_settings()
app = FastAPI(
    title="InvoiceOps",
    version=settings.service_version,
    lifespan=lifespan,
)
app.include_router(health_router)
