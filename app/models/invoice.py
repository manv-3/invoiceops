"""Invoice domain models for the InvoiceOps workflow."""

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    """Return the current UTC datetime."""
    return datetime.now(UTC)


class LineItem(BaseModel):
    """A single line item extracted from an invoice."""

    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None


class InvoiceData(BaseModel):
    """Structured fields extracted from an invoice document."""

    vendor_name: str | None = None
    vendor_id: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    purchase_order_ref: str | None = None
    payment_details: str | None = None
    line_items: list[LineItem] = []


class ExtractionResult(BaseModel):
    """Wraps extracted invoice data with confidence information."""

    data: InvoiceData
    confidence: float = Field(ge=0.0, le=1.0)
    field_confidences: dict[str, float] = {}
    raw_text: str | None = None


class InvoiceStatus(StrEnum):
    """Lifecycle states for an invoice in the workflow."""

    RECEIVED = "received"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    VALIDATING = "validating"
    VALIDATED = "validated"
    NEEDS_REVIEW = "needs_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    ACTION_CREATED = "action_created"
    FAILED = "failed"


class ValidationFinding(BaseModel):
    """A single finding produced by a validation rule."""

    rule: str
    severity: Literal["error", "warning", "info"]
    message: str
    field: str | None = None


class ValidationResult(BaseModel):
    """Aggregate result of all validation rules for an invoice."""

    passed: bool
    findings: list[ValidationFinding] = []
    needs_review: bool = False


class ReviewDecision(BaseModel):
    """A human review decision for an invoice requiring attention."""

    decision: Literal["approve", "reject", "override"]
    reviewer_note: str | None = None
    overrides: dict[str, Any] | None = None
    decided_at: datetime = Field(default_factory=_utcnow)


class InvoiceRecord(BaseModel):
    """Full lifecycle record for a single invoice."""

    id: str = Field(default_factory=lambda: str(uuid4()))
    status: InvoiceStatus = InvoiceStatus.RECEIVED
    original_filename: str
    stored_path: str
    fingerprint: str
    extraction: ExtractionResult | None = None
    validation: ValidationResult | None = None
    review_decision: ReviewDecision | None = None
    action_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    notion_page_id: str | None = None


class ApprovalPacket(BaseModel):
    """Payment-ready approval packet generated for an approved invoice."""

    action_id: str = Field(default_factory=lambda: str(uuid4()))
    invoice_id: str
    vendor_name: str
    invoice_number: str
    total: Decimal
    currency: str
    payment_details: str | None = None
    approved_at: datetime = Field(default_factory=_utcnow)
    approval_source: Literal["auto", "reviewer"]


class ReviewRequest(BaseModel):
    """Inbound request body for submitting a review decision."""

    decision: Literal["approve", "reject", "override"]
    reviewer_note: str | None = None
    overrides: dict[str, Any] | None = None
