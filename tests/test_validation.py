"""Tests for the validation service."""

from decimal import Decimal

from app.models.invoice import (
    ExtractionResult,
    InvoiceData,
    InvoiceRecord,
)
from app.services import store
from app.services.validation import validate_invoice


def _make_record(**kwargs) -> InvoiceRecord:  # type: ignore[no-untyped-def]
    defaults = {
        "original_filename": "test.pdf",
        "stored_path": "storage/incoming/test.pdf",
        "fingerprint": "unique-fp",
    }
    defaults.update(kwargs)
    return InvoiceRecord(**defaults)


def _make_extraction(**overrides) -> ExtractionResult:  # type: ignore[no-untyped-def]
    defaults: dict = {
        "vendor_name": "Acme Corp",
        "invoice_number": "INV-001",
        "currency": "USD",
        "subtotal": Decimal("100.00"),
        "tax": Decimal("10.00"),
        "total": Decimal("110.00"),
    }
    defaults.update(overrides)
    return ExtractionResult(data=InvoiceData(**defaults), confidence=0.95)


def setup_function() -> None:
    store.clear()


def test_valid_invoice_passes() -> None:
    record = _make_record()
    extraction = _make_extraction()
    result = validate_invoice(record, extraction)
    assert result.passed is True
    assert result.needs_review is False


def test_missing_required_field_fails() -> None:
    record = _make_record()
    extraction = _make_extraction(vendor_name=None)
    result = validate_invoice(record, extraction)
    assert result.passed is False
    assert any(f.rule == "required_field" for f in result.findings)


def test_total_mismatch_fails() -> None:
    record = _make_record()
    extraction = _make_extraction(
        subtotal=Decimal("100.00"),
        tax=Decimal("10.00"),
        total=Decimal("200.00"),
    )
    result = validate_invoice(record, extraction)
    assert result.passed is False
    assert any(f.rule == "total_mismatch" for f in result.findings)


def test_low_confidence_warns() -> None:
    record = _make_record()
    extraction = ExtractionResult(
        data=InvoiceData(
            vendor_name="Acme",
            invoice_number="INV-002",
            currency="USD",
            total=Decimal("100.00"),
        ),
        confidence=0.50,
    )
    result = validate_invoice(record, extraction)
    assert result.needs_review is True
    assert any(f.rule == "low_confidence" for f in result.findings)


def test_duplicate_fingerprint_detected() -> None:
    record1 = _make_record(fingerprint="same-fp")
    store.save(record1)

    record2 = _make_record(fingerprint="same-fp")
    extraction = _make_extraction()
    result = validate_invoice(record2, extraction)
    assert any(f.rule == "duplicate_fingerprint" for f in result.findings)
