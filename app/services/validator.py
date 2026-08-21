"""Deterministic invoice validation. No model output makes workflow decisions here."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.models.invoice import InvoiceExtraction
from app.services.vendor_service import VendorMatch


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["info", "warning", "high"]
    message: str


class ValidationResult(BaseModel):
    status: Literal["Passed", "Warning", "Failed", "Duplicate"]
    issue_codes: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    requires_review: bool = False
    machine_reasoning: str = "No validation issues were found."


class Validator:
    def __init__(
        self,
        min_confidence: Decimal = Decimal("0.85"),
        rounding_tolerance: Decimal = Decimal("0.01"),
    ) -> None:
        self.min_confidence = min_confidence
        self.rounding_tolerance = rounding_tolerance

    def validate(
        self,
        extraction: InvoiceExtraction,
        *,
        duplicate_reason: str | None = None,
        match: VendorMatch | None = None,
        po_matches: bool | None = None,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        risk_flags: list[str] = []

        if duplicate_reason:
            code = (
                "DUPLICATE_FINGERPRINT"
                if "fingerprint" in duplicate_reason.casefold()
                else "DUPLICATE_VENDOR_INVOICE"
            )
            issues.append(ValidationIssue(code=code, severity="high", message=duplicate_reason))
            risk_flags.append("Possible Duplicate")
            return self._result("Duplicate", issues, risk_flags, requires_review=False)

        required = (
            ("vendor_name", extraction.vendor_name, "MISSING_VENDOR_NAME"),
            ("invoice_number", extraction.invoice_number, "MISSING_INVOICE_NUMBER"),
            ("invoice_date", extraction.invoice_date, "MISSING_INVOICE_DATE"),
            ("total", extraction.total, "MISSING_TOTAL"),
        )
        for field_name, value, code in required:
            if value is None or value == "":
                issues.append(
                    ValidationIssue(
                        code=code,
                        severity="high",
                        message=f"Required field {field_name.replace('_', ' ')} is missing",
                    )
                )
        if any(issue.code.startswith("MISSING_") for issue in issues):
            risk_flags.append("Missing Fields")

        if all(
            value is not None for value in (extraction.subtotal, extraction.tax, extraction.total)
        ):
            expected = extraction.subtotal + extraction.tax
            difference = abs(expected - extraction.total)
            if difference > self.rounding_tolerance:
                issues.append(
                    ValidationIssue(
                        code="TOTAL_MISMATCH",
                        severity="high",
                        message=(
                            f"Subtotal plus tax ({expected}) differs from total "
                            f"({extraction.total}) by {difference}"
                        ),
                    )
                )
                risk_flags.append("Total Mismatch")

        if (
            extraction.overall_confidence is None
            or extraction.overall_confidence < self.min_confidence
        ):
            issues.append(
                ValidationIssue(
                    code="LOW_CONFIDENCE",
                    severity="warning",
                    message="Extraction confidence is below the automatic-processing threshold",
                )
            )
            risk_flags.append("Low Confidence")

        if match is not None:
            if match.new_vendor:
                issues.append(
                    ValidationIssue(
                        code="NEW_VENDOR",
                        severity="high",
                        message="Vendor was created from this invoice and is not yet trusted",
                    )
                )
                risk_flags.append("Unknown Vendor")
            if match.vendor is None:
                issues.append(
                    ValidationIssue(
                        code="UNKNOWN_VENDOR",
                        severity="high",
                        message=match.reason or "No vendor record matched the invoice",
                    )
                )
                risk_flags.append("Unknown Vendor")
            if match.vendor_mismatch:
                issues.append(
                    ValidationIssue(
                        code="VENDOR_MISMATCH",
                        severity="high",
                        message="Extracted vendor identity differs from the vendor record",
                    )
                )
                risk_flags.append("Unknown Vendor")
            if match.bank_details_changed:
                issues.append(
                    ValidationIssue(
                        code="BANK_DETAILS_CHANGED",
                        severity="high",
                        message=(
                            "Extracted bank reference differs from the approved vendor reference"
                        ),
                    )
                )
                risk_flags.append("Bank Details Changed")

        if po_matches is False and extraction.po_number:
            issues.append(
                ValidationIssue(
                    code="PO_MISMATCH",
                    severity="high",
                    message="The extracted purchase-order reference did not match the known PO",
                )
            )
            risk_flags.append("PO Mismatch")

        has_high = any(issue.severity == "high" for issue in issues)
        status = "Failed" if has_high else "Warning" if issues else "Passed"
        return self._result(status, issues, risk_flags, requires_review=bool(issues))

    @staticmethod
    def _result(
        status: Literal["Passed", "Warning", "Failed", "Duplicate"],
        issues: list[ValidationIssue],
        risk_flags: list[str],
        *,
        requires_review: bool,
    ) -> ValidationResult:
        unique_flags = list(dict.fromkeys(risk_flags))
        reasoning = " ".join(issue.message + "." for issue in issues)
        return ValidationResult(
            status=status,
            issue_codes=[issue.code for issue in issues],
            issues=issues,
            risk_flags=unique_flags,
            requires_review=requires_review,
            machine_reasoning=reasoning or "No validation issues were found.",
        )
