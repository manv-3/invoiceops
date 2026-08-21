"""Deterministic validation rules for extracted invoice data.

AI extracts; this module decides.  All monetary comparisons use ``Decimal``.
"""

from __future__ import annotations

import logging

from app.models.invoice import (
    ExtractionResult,
    InvoiceRecord,
    ValidationFinding,
    ValidationResult,
)
from app.services import store
from app.utils.money import check_total

logger = logging.getLogger(__name__)

# Minimum confidence score before flagging for review.
CONFIDENCE_THRESHOLD = 0.80

REQUIRED_FIELDS = ["vendor_name", "invoice_number", "total", "currency"]


def _check_required_fields(data_dict: dict) -> list[ValidationFinding]:
    """Flag any required field that is missing or null."""
    findings: list[ValidationFinding] = []
    for field in REQUIRED_FIELDS:
        if not data_dict.get(field):
            findings.append(
                ValidationFinding(
                    rule="required_field",
                    severity="error",
                    message=f"Required field '{field}' is missing.",
                    field=field,
                )
            )
    return findings


def _check_math(data_dict: dict) -> list[ValidationFinding]:
    """Verify subtotal + tax == total."""
    findings: list[ValidationFinding] = []
    subtotal = data_dict.get("subtotal")
    tax = data_dict.get("tax")
    total = data_dict.get("total")
    if subtotal is not None and total is not None:
        if not check_total(subtotal, tax, total):
            findings.append(
                ValidationFinding(
                    rule="total_mismatch",
                    severity="error",
                    message="Subtotal + tax does not equal total.",
                    field="total",
                )
            )
    return findings


def _check_duplicate_fingerprint(record: InvoiceRecord) -> list[ValidationFinding]:
    """Check if another invoice with the same document fingerprint exists."""
    findings: list[ValidationFinding] = []
    existing = store.get_by_fingerprint(record.fingerprint)
    if existing is not None and existing.id != record.id:
        findings.append(
            ValidationFinding(
                rule="duplicate_fingerprint",
                severity="error",
                message=(f"Duplicate document detected (matches invoice {existing.id})."),
                field="fingerprint",
            )
        )
    return findings


def _check_duplicate_vendor_invoice(
    extraction: ExtractionResult, record_id: str
) -> list[ValidationFinding]:
    """Check for duplicate vendor + invoice-number combination."""
    findings: list[ValidationFinding] = []
    data = extraction.data
    existing = store.find_by_vendor_and_number(data.vendor_name, data.invoice_number)
    if existing is not None and existing.id != record_id:
        findings.append(
            ValidationFinding(
                rule="duplicate_vendor_invoice",
                severity="error",
                message=(
                    f"Duplicate vendor/invoice-number combination (matches invoice {existing.id})."
                ),
                field="invoice_number",
            )
        )
    return findings


def _check_confidence(extraction: ExtractionResult) -> list[ValidationFinding]:
    """Flag low overall extraction confidence."""
    findings: list[ValidationFinding] = []
    if extraction.confidence < CONFIDENCE_THRESHOLD:
        findings.append(
            ValidationFinding(
                rule="low_confidence",
                severity="warning",
                message=(
                    f"Extraction confidence {extraction.confidence:.2f} "
                    f"is below threshold {CONFIDENCE_THRESHOLD}."
                ),
                field=None,
            )
        )
    return findings


def validate_invoice(record: InvoiceRecord, extraction: ExtractionResult) -> ValidationResult:
    """Run all validation rules against *extraction* and *record*.

    Returns a ``ValidationResult`` summarising findings and whether the invoice
    requires human review.
    """
    data_dict = extraction.data.model_dump()
    findings: list[ValidationFinding] = []

    findings.extend(_check_required_fields(data_dict))
    findings.extend(_check_math(data_dict))
    findings.extend(_check_duplicate_fingerprint(record))
    findings.extend(_check_duplicate_vendor_invoice(extraction, record.id))
    findings.extend(_check_confidence(extraction))

    has_errors = any(f.severity == "error" for f in findings)
    has_warnings = any(f.severity == "warning" for f in findings)
    needs_review = has_errors or has_warnings

    return ValidationResult(
        passed=not has_errors,
        findings=findings,
        needs_review=needs_review,
    )
