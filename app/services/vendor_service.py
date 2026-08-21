"""Vendor lookup and payment-reference comparison."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from app.models.invoice import InvoiceExtraction, VendorRecord


class VendorLookup(Protocol):
    async def find_vendor(
        self, vendor_name: str | None, gstin: str | None = None
    ) -> VendorRecord | None: ...

    async def create_vendor(self, vendor: VendorRecord) -> VendorRecord: ...


@dataclass(frozen=True)
class VendorMatch:
    vendor: VendorRecord | None
    new_vendor: bool = False
    vendor_mismatch: bool = False
    bank_details_changed: bool = False
    reason: str | None = None


def _normalized(value: str | None) -> str:
    return " ".join((value or "").casefold().split())


class VendorService:
    def __init__(self, notion: VendorLookup) -> None:
        self.notion = notion

    async def match_vendor(self, extraction: InvoiceExtraction) -> VendorMatch:
        if not extraction.vendor_name:
            return VendorMatch(None, reason="Vendor name was not extracted")
        vendor = await self.notion.find_vendor(extraction.vendor_name, extraction.gstin)
        if vendor is None:
            created = await self.notion.create_vendor(
                VendorRecord(
                    vendor_id=f"VENDOR-{uuid4().hex}",
                    vendor_name=extraction.vendor_name,
                    gstin=extraction.gstin,
                    trusted_vendor=False,
                    risk_notes=(
                        "Created from invoice intake; identity and payment details require human "
                        "approval"
                    ),
                )
            )
            return VendorMatch(
                vendor=created,
                new_vendor=True,
                # A new vendor has no approved payment reference to compare against.
                # It is still high risk, but this is not a changed-bank-details event.
                bank_details_changed=False,
                reason="New vendor record created; identity and payment details require review",
            )
        name_mismatch = _normalized(vendor.vendor_name) != _normalized(extraction.vendor_name)
        gstin_mismatch = bool(
            vendor.gstin and extraction.gstin and vendor.gstin != extraction.gstin
        )
        bank_changed = bool(
            vendor.approved_bank_account_reference
            and extraction.bank_account_reference
            and vendor.approved_bank_account_reference != extraction.bank_account_reference
        )
        reasons = []
        if name_mismatch or gstin_mismatch:
            reasons.append("vendor identity differs from the trusted vendor record")
        if bank_changed:
            reasons.append("extracted bank reference differs from the approved reference")
        return VendorMatch(
            vendor=vendor,
            vendor_mismatch=name_mismatch or gstin_mismatch,
            bank_details_changed=bank_changed,
            reason="; ".join(reasons) or None,
        )
