"""Liveness endpoint for the InvoiceOps service."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.models.health import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Check service health")
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Return service identity without contacting external providers."""

    return HealthResponse(version=settings.service_version)
