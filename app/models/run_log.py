"""Run Log entry model for audit trail."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class RunLogEntry(BaseModel):
    """A single entry in the InvoiceOps audit trail."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    invoice_id: str
    timestamp: datetime = Field(default_factory=_utcnow)
    event: str
    detail: str | None = None
    metadata: dict[str, Any] | None = None
