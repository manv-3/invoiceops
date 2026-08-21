"""Health response contract."""

from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Stable response returned by the liveness endpoint."""

    status: Literal["ok"] = "ok"
    service: Literal["invoiceops"] = "invoiceops"
    version: str
