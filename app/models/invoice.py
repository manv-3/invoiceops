"""Typed invoice, vendor, and audit models shared by application services."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.models.workflow import HumanDecision, ProcessingStatus


def utc_now() -> datetime:
    """Return an aware UTC timestamp."""

    return datetime.now(UTC)


class LineItem(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    tax: Decimal | None = None
    amount: Decimal | None = None


class FieldConfidence(BaseModel):
    """Fixed confidence keys so Gemini can generate a closed JSON object."""

    vendor_name: Decimal | None = Field(default=None, ge=0, le=1)
    gstin: Decimal | None = Field(default=None, ge=0, le=1)
    invoice_number: Decimal | None = Field(default=None, ge=0, le=1)
    invoice_date: Decimal | None = Field(default=None, ge=0, le=1)
    due_date: Decimal | None = Field(default=None, ge=0, le=1)
    currency: Decimal | None = Field(default=None, ge=0, le=1)
    subtotal: Decimal | None = Field(default=None, ge=0, le=1)
    tax: Decimal | None = Field(default=None, ge=0, le=1)
    total: Decimal | None = Field(default=None, ge=0, le=1)
    po_number: Decimal | None = Field(default=None, ge=0, le=1)
    bank_account_reference: Decimal | None = Field(default=None, ge=0, le=1)
    line_items: Decimal | None = Field(default=None, ge=0, le=1)


class InvoiceExtraction(BaseModel):
    """Values extracted from a document; unknown values remain ``None``."""

    model_config = ConfigDict(extra="forbid")

    vendor_name: str | None = None
    gstin: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    po_number: str | None = None
    bank_account_reference: str | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    overall_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    field_confidence: FieldConfidence = Field(default_factory=FieldConfidence)


class GeminiInvoiceExtraction(InvoiceExtraction):
    """Provider-facing variant without unsupported JSON-schema extra-property flags."""

    model_config = ConfigDict(extra="ignore")


class InvoiceRecord(BaseModel):
    """Application representation of an Invoice Notion row."""

    model_config = ConfigDict(use_enum_values=False)

    invoice_id: str
    page_id: str | None = None
    invoice_number: str | None = None
    vendor_name: str | None = None
    vendor_id: str | None = None
    vendor_page_id: str | None = None
    gstin: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    corrected_total: Decimal | None = None
    po_number: str | None = None
    bank_account_reference: str | None = None
    document_fingerprint: str | None = None
    source: str = "Upload"
    source_url: str | None = None
    extraction_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    processing_status: ProcessingStatus = ProcessingStatus.RECEIVED
    human_decision: HumanDecision = HumanDecision.PENDING
    validation_status: str = "Pending"
    risk_flags: list[str] = Field(default_factory=list)
    machine_reasoning: str | None = None
    reviewer_notes: str | None = None
    external_action_status: str = "Not Started"
    external_action_id: str | None = None
    decision_processed_at: datetime | None = None
    received_at: datetime = Field(default_factory=utc_now)
    reviewed_at: datetime | None = None
    approved_at: datetime | None = None
    last_updated: datetime = Field(default_factory=utc_now)


class InvoiceUpdate(BaseModel):
    """Explicit writable fields for an Invoice row."""

    page_id: str | None = None
    invoice_number: str | None = None
    vendor_name: str | None = None
    vendor_id: str | None = None
    vendor_page_id: str | None = None
    gstin: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    corrected_total: Decimal | None = None
    po_number: str | None = None
    bank_account_reference: str | None = None
    document_fingerprint: str | None = None
    source: str | None = None
    source_url: str | None = None
    extraction_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    processing_status: ProcessingStatus | None = None
    human_decision: HumanDecision | None = None
    validation_status: str | None = None
    risk_flags: list[str] | None = None
    machine_reasoning: str | None = None
    reviewer_notes: str | None = None
    external_action_status: str | None = None
    external_action_id: str | None = None
    decision_processed_at: datetime | None = None
    received_at: datetime | None = None
    reviewed_at: datetime | None = None
    approved_at: datetime | None = None
    last_updated: datetime | None = None


class VendorRecord(BaseModel):
    vendor_id: str
    page_id: str | None = None
    vendor_name: str
    gstin: str | None = None
    email: str | None = None
    phone: str | None = None
    approved_bank_account_reference: str | None = None
    payment_terms: str | None = None
    active_status: str = "Active"
    trusted_vendor: bool = False
    risk_notes: str | None = None


class DuplicateMatch(BaseModel):
    invoice_id: str
    page_id: str | None = None
    reason: str


class RunLogEntry(BaseModel):
    run_id: str = Field(default_factory=lambda: f"RUN-{uuid4().hex}")
    invoice_id: str
    page_id: str | None = None
    timestamp: datetime = Field(default_factory=utc_now)
    trigger: str
    stage: str
    result: str
    summary: str
    error_message: str | None = None
    external_action: str | None = None
    duration_ms: int | None = None
    service_version: str = "0.1.0"
    retry_count: int = 0
    invoice_page_id: str | None = None


class InvoiceStatusResponse(BaseModel):
    invoice_id: str
    page_id: str | None = None
    processing_status: ProcessingStatus
    human_decision: HumanDecision
    document_fingerprint: str | None = None
    received_at: datetime | None = None
    last_updated: datetime | None = None


class InvoiceIntakeResponse(BaseModel):
    invoice_id: str
    page_id: str | None = None
    processing_status: ProcessingStatus
    document_fingerprint: str
    source: str


def new_invoice_id() -> str:
    """Generate a non-guessable application invoice identifier."""

    return f"INVOPS-{uuid4().hex}"


def model_dump_for_debug(model: BaseModel) -> dict[str, Any]:
    """Return a safe typed model dump for diagnostics and tests."""

    return model.model_dump(mode="json")
