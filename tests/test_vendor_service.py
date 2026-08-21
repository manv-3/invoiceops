import asyncio

from app.models.invoice import InvoiceExtraction, VendorRecord
from app.services.vendor_service import VendorService


class FakeNotion:
    async def find_vendor(self, vendor_name: str, gstin: str | None = None) -> VendorRecord | None:
        return VendorRecord(
            vendor_id="V-1",
            page_id="vendor-page",
            vendor_name="Acme Corporation",
            gstin="GST-1",
            approved_bank_account_reference="bank-1",
        )

    async def create_vendor(self, vendor: VendorRecord) -> VendorRecord:
        return vendor.model_copy(update={"page_id": "created-vendor-page"})


def test_vendor_service_reports_name_mismatch_and_bank_change() -> None:
    service = VendorService(FakeNotion())
    extraction = InvoiceExtraction(
        vendor_name="Different Name",
        gstin="GST-1",
        bank_account_reference="bank-2",
    )

    match = asyncio.run(service.match_vendor(extraction))

    assert match.vendor is not None
    assert match.vendor_mismatch is True
    assert match.bank_details_changed is True


class MissingVendorNotion:
    async def find_vendor(self, vendor_name: str, gstin: str | None = None) -> VendorRecord | None:
        return None

    async def create_vendor(self, vendor: VendorRecord) -> VendorRecord:
        return vendor.model_copy(update={"page_id": "created-vendor-page"})


def test_unknown_vendor_is_created_untrusted_and_requires_review() -> None:
    service = VendorService(MissingVendorNotion())
    extraction = InvoiceExtraction(
        vendor_name="New Supplier",
        gstin="GST-NEW",
        bank_account_reference="new-bank-reference",
    )

    match = asyncio.run(service.match_vendor(extraction))

    assert match.vendor is not None
    assert match.new_vendor is True
    assert match.vendor.trusted_vendor is False
    assert match.vendor.approved_bank_account_reference is None
    assert match.bank_details_changed is False
