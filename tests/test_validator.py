from datetime import date
from decimal import Decimal

from app.models.invoice import InvoiceExtraction, VendorRecord
from app.services.validator import Validator
from app.services.vendor_service import VendorMatch


def extraction(**overrides: object) -> InvoiceExtraction:
    values: dict[str, object] = {
        "vendor_name": "Acme",
        "invoice_number": "A-1",
        "invoice_date": date(2026, 1, 1),
        "subtotal": Decimal("10.00"),
        "tax": Decimal("1.80"),
        "total": Decimal("11.80"),
        "overall_confidence": Decimal("0.95"),
    }
    values.update(overrides)
    return InvoiceExtraction.model_validate(values)


def validator() -> Validator:
    return Validator(min_confidence=Decimal("0.85"), rounding_tolerance=Decimal("0.01"))


def test_valid_invoice_passes_without_review() -> None:
    result = validator().validate(extraction())

    assert result.status == "Passed"
    assert result.requires_review is False
    assert result.issue_codes == []


def test_missing_required_field_requires_review() -> None:
    result = validator().validate(extraction(invoice_number=None))

    assert result.requires_review is True
    assert "MISSING_INVOICE_NUMBER" in result.issue_codes


def test_arithmetic_mismatch_is_failed_and_requires_review() -> None:
    result = validator().validate(extraction(total=Decimal("12.00")))

    assert result.status == "Failed"
    assert "TOTAL_MISMATCH" in result.issue_codes
    assert result.requires_review is True


def test_low_confidence_requires_review() -> None:
    result = validator().validate(extraction(overall_confidence=Decimal("0.40")))

    assert result.status == "Warning"
    assert "LOW_CONFIDENCE" in result.issue_codes
    assert result.requires_review is True


def test_changed_bank_details_always_require_review() -> None:
    vendor = VendorRecord(
        vendor_id="V-1",
        vendor_name="Acme",
        approved_bank_account_reference="bank-old",
    )
    match = VendorMatch(vendor=vendor, bank_details_changed=True)

    result = validator().validate(extraction(bank_account_reference="bank-new"), match=match)

    assert result.requires_review is True
    assert "BANK_DETAILS_CHANGED" in result.issue_codes


def test_new_vendor_always_requires_review() -> None:
    vendor = VendorRecord(vendor_id="V-NEW", vendor_name="New Supplier", trusted_vendor=False)
    match = VendorMatch(vendor=vendor, new_vendor=True)

    result = validator().validate(extraction(), match=match)

    assert result.requires_review is True
    assert "NEW_VENDOR" in result.issue_codes


def test_duplicate_fingerprint_is_terminal_duplicate() -> None:
    result = validator().validate(
        extraction(), duplicate_reason="Document fingerprint already exists"
    )

    assert result.status == "Duplicate"
    assert result.requires_review is False
    assert "DUPLICATE_FINGERPRINT" in result.issue_codes
