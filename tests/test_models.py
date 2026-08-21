"""Tests for Pydantic models."""

from decimal import Decimal

from app.models.invoice import (
    ApprovalPacket,
    ExtractionResult,
    InvoiceData,
    InvoiceRecord,
    InvoiceStatus,
    LineItem,
    ReviewDecision,
    ValidationFinding,
    ValidationResult,
)
from app.models.run_log import RunLogEntry


def test_line_item_defaults() -> None:
    item = LineItem()
    assert item.description is None
    assert item.amount is None


def test_invoice_data_with_values() -> None:
    data = InvoiceData(
        vendor_name="Acme",
        total=Decimal("500.00"),
        currency="USD",
    )
    assert data.vendor_name == "Acme"
    assert data.total == Decimal("500.00")
    assert data.line_items == []


def test_extraction_result_confidence_range() -> None:
    result = ExtractionResult(data=InvoiceData(), confidence=0.95)
    assert result.confidence == 0.95


def test_invoice_record_has_uuid() -> None:
    record = InvoiceRecord(
        original_filename="test.pdf",
        stored_path="storage/incoming/abc.pdf",
        fingerprint="abc123",
    )
    assert len(record.id) == 36  # UUID with hyphens
    assert record.status == InvoiceStatus.RECEIVED


def test_validation_finding_severity() -> None:
    finding = ValidationFinding(
        rule="test_rule",
        severity="error",
        message="Something wrong",
    )
    assert finding.severity == "error"


def test_validation_result_defaults() -> None:
    result = ValidationResult(passed=True)
    assert result.findings == []
    assert result.needs_review is False


def test_review_decision_has_timestamp() -> None:
    decision = ReviewDecision(decision="approve")
    assert decision.decided_at is not None


def test_approval_packet_has_action_id() -> None:
    packet = ApprovalPacket(
        invoice_id="inv-1",
        vendor_name="Acme",
        invoice_number="INV-001",
        total=Decimal("100.00"),
        currency="USD",
        approval_source="auto",
    )
    assert len(packet.action_id) == 36


def test_run_log_entry_defaults() -> None:
    entry = RunLogEntry(invoice_id="inv-1", event="test")
    assert entry.detail is None
    assert entry.metadata is None
    assert entry.timestamp is not None
