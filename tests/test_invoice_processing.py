import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.config import Settings
from app.models.invoice import (
    InvoiceExtraction,
    InvoiceRecord,
    InvoiceUpdate,
    RunLogEntry,
    VendorRecord,
)
from app.models.workflow import ProcessingStatus
from app.services.invoice_processing import InvoiceProcessingService


class FakeExtractor:
    def __init__(self, value: InvoiceExtraction) -> None:
        self.value = value

    async def extract(self, file_path: Path) -> InvoiceExtraction:
        return self.value


class FakeNotion:
    def __init__(self, invoice: InvoiceRecord, duplicate=None, match=None) -> None:
        self.invoice = invoice
        self.duplicate = duplicate
        self.vendor = match
        self.logs: list[RunLogEntry] = []
        self.created_vendors = 0

    async def get_invoice(self, invoice_id: str) -> InvoiceRecord | None:
        return self.invoice if invoice_id == self.invoice.invoice_id else None

    async def find_invoice_duplicate(self, *args, **kwargs):
        return self.duplicate

    async def find_vendor(self, vendor_name: str, gstin: str | None = None):
        return self.vendor

    async def create_vendor(self, vendor: VendorRecord) -> VendorRecord:
        self.created_vendors += 1
        return vendor.model_copy(update={"page_id": vendor.page_id or "created-vendor"})

    async def update_invoice(self, invoice_id: str, updates: InvoiceUpdate) -> InvoiceRecord:
        self.invoice = self.invoice.model_copy(update=updates.model_dump(exclude_unset=True))
        return self.invoice

    async def create_run_log(self, entry: RunLogEntry, invoice_page_id: str | None = None):
        self.logs.append(entry)
        return entry


def base_extraction() -> InvoiceExtraction:
    return InvoiceExtraction(
        vendor_name="Acme",
        invoice_number="A-1",
        invoice_date=date(2026, 1, 1),
        subtotal=Decimal("10.00"),
        tax=Decimal("1.80"),
        total=Decimal("11.80"),
        overall_confidence=Decimal("0.95"),
    )


def service(
    tmp_path: Path, notion: FakeNotion, extraction: InvoiceExtraction
) -> InvoiceProcessingService:
    settings = Settings(_env_file=None, storage_dir=tmp_path, auto_process_clean_invoices=True)
    return InvoiceProcessingService(notion, settings, extractor=FakeExtractor(extraction))


def test_clean_invoice_completes_with_packet(tmp_path: Path) -> None:
    invoice = InvoiceRecord(invoice_id="INV-1", page_id="page-1", document_fingerprint="a" * 64)
    notion = FakeNotion(
        invoice, match=VendorRecord(vendor_id="V-1", page_id="vendor-1", vendor_name="Acme")
    )
    result = asyncio.run(
        service(tmp_path, notion, base_extraction()).process("INV-1", tmp_path / "invoice.pdf")
    )

    assert result.processing_status is ProcessingStatus.COMPLETED
    assert result.human_decision.value == "Pending"
    assert result.external_action_id is not None
    assert len(list((tmp_path / "approved").glob("*.json"))) == 1


def test_arithmetic_mismatch_routes_to_review(tmp_path: Path) -> None:
    invoice = InvoiceRecord(invoice_id="INV-2", page_id="page-2", document_fingerprint="b" * 64)
    notion = FakeNotion(
        invoice, match=VendorRecord(vendor_id="V-1", page_id="vendor-1", vendor_name="Acme")
    )
    extraction = base_extraction().model_copy(update={"total": Decimal("12.00")})
    result = asyncio.run(
        service(tmp_path, notion, extraction).process("INV-2", tmp_path / "invoice.pdf")
    )

    assert result.processing_status is ProcessingStatus.NEEDS_REVIEW
    assert "Total Mismatch" in result.risk_flags


def test_duplicate_routes_to_duplicate_state(tmp_path: Path) -> None:
    invoice = InvoiceRecord(invoice_id="INV-3", page_id="page-3", document_fingerprint="c" * 64)
    notion = FakeNotion(
        invoice,
        duplicate=type("Duplicate", (), {"reason": "Document fingerprint already exists"})(),
        match=None,
    )
    result = asyncio.run(
        service(tmp_path, notion, base_extraction()).process("INV-3", tmp_path / "invoice.pdf")
    )

    assert result.processing_status is ProcessingStatus.DUPLICATE
    assert notion.created_vendors == 0


def test_validating_invoice_can_resume_after_transient_write_failure(tmp_path: Path) -> None:
    invoice = InvoiceRecord(
        invoice_id="INV-4",
        page_id="page-4",
        document_fingerprint="d" * 64,
        processing_status=ProcessingStatus.VALIDATING,
    )
    notion = FakeNotion(
        invoice, match=VendorRecord(vendor_id="V-1", page_id="vendor-1", vendor_name="Acme")
    )

    result = asyncio.run(
        service(tmp_path, notion, base_extraction()).process("INV-4", tmp_path / "invoice.pdf")
    )

    assert result.processing_status is ProcessingStatus.COMPLETED


def test_unknown_vendor_is_created_and_routes_to_review(tmp_path: Path) -> None:
    invoice = InvoiceRecord(invoice_id="INV-5", page_id="page-5", document_fingerprint="e" * 64)
    notion = FakeNotion(invoice, match=None)

    result = asyncio.run(
        service(tmp_path, notion, base_extraction()).process("INV-5", tmp_path / "invoice.pdf")
    )

    assert result.processing_status is ProcessingStatus.NEEDS_REVIEW
    assert result.vendor_page_id == "created-vendor"
    assert "Unknown Vendor" in result.risk_flags
