"""Invoice extraction, deterministic validation, and workflow routing."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from app.config import Settings
from app.models.invoice import InvoiceExtraction, InvoiceRecord, InvoiceUpdate, RunLogEntry, utc_now
from app.models.workflow import ProcessingStatus
from app.services.action_service import ActionPacketService
from app.services.extractor import ExtractionError, InvoiceExtractor
from app.services.validator import Validator
from app.services.vendor_service import VendorService
from app.services.workflow_service import WorkflowService


class ProcessingNotion(Protocol):
    async def get_invoice(self, invoice_id: str) -> InvoiceRecord | None: ...

    async def update_invoice(self, invoice_id: str, updates: InvoiceUpdate) -> InvoiceRecord: ...

    async def create_run_log(
        self, entry: RunLogEntry, invoice_page_id: str | None = None
    ) -> RunLogEntry: ...

    async def find_invoice_duplicate(
        self,
        document_fingerprint: str | None,
        vendor_name: str | None,
        invoice_number: str | None,
        exclude_invoice_id: str | None = None,
    ): ...

    async def find_vendor(self, vendor_name: str, gstin: str | None = None): ...


class InvoiceProcessingService:
    def __init__(
        self,
        notion: ProcessingNotion,
        settings: Settings,
        *,
        extractor: Any | None = None,
        validator: Validator | None = None,
    ) -> None:
        self.notion = notion
        self.settings = settings
        self.extractor = extractor or InvoiceExtractor(settings)
        self.validator = validator or Validator()
        self.vendor_service = VendorService(notion)
        self.workflow = WorkflowService(notion, settings.service_version)
        self.actions = ActionPacketService(settings.storage_dir / "approved")

    async def process(self, invoice_id: str, file_path: Path) -> InvoiceRecord:
        invoice = await self.notion.get_invoice(invoice_id)
        if invoice is None:
            raise LookupError(f"invoice {invoice_id} was not found")
        if invoice.processing_status is ProcessingStatus.RECEIVED:
            invoice = await self.workflow.transition(
                invoice,
                ProcessingStatus.EXTRACTING,
                trigger="Upload",
                stage="Extraction",
                summary="Started structured document extraction",
            )
        elif invoice.processing_status not in {
            ProcessingStatus.EXTRACTING,
            ProcessingStatus.VALIDATING,
        }:
            return invoice
        try:
            extraction = await self.extractor.extract(file_path)
        except ExtractionError as exc:
            await self.workflow.transition(
                invoice,
                ProcessingStatus.FAILED,
                trigger="Upload",
                stage="Extraction",
                result="Failed",
                summary="Document extraction failed",
            )
            raise exc

        if invoice.processing_status is ProcessingStatus.EXTRACTING:
            invoice = await self.workflow.transition(
                invoice,
                ProcessingStatus.VALIDATING,
                trigger="Upload",
                stage="Validation",
                summary="Started deterministic validation",
            )
        duplicate = await self.notion.find_invoice_duplicate(
            invoice.document_fingerprint,
            extraction.vendor_name,
            extraction.invoice_number,
            exclude_invoice_id=invoice.invoice_id,
        )
        match = None if duplicate else await self.vendor_service.match_vendor(extraction)
        validation = self.validator.validate(
            extraction,
            duplicate_reason=duplicate.reason if duplicate else None,
            match=match,
        )
        invoice = await self.notion.update_invoice(
            invoice.invoice_id,
            _extraction_update(extraction, match, validation, invoice),
        )

        if validation.status == "Duplicate":
            return await self.workflow.transition(
                invoice,
                ProcessingStatus.DUPLICATE,
                trigger="Upload",
                stage="Duplicate Check",
                summary=validation.machine_reasoning,
                additional_updates=InvoiceUpdate(validation_status="Failed"),
            )
        if validation.requires_review or not self.settings.auto_process_clean_invoices:
            return await self.workflow.transition(
                invoice,
                ProcessingStatus.NEEDS_REVIEW,
                trigger="Upload",
                stage="Human Review",
                summary=(
                    validation.machine_reasoning
                    if validation.requires_review
                    else "Automatic processing is disabled; human review is required"
                ),
                additional_updates=InvoiceUpdate(human_decision="Pending"),
            )

        invoice = await self.notion.update_invoice(
            invoice.invoice_id,
            InvoiceUpdate(validation_status="Passed"),
        )
        packet = self.actions.generate(
            invoice,
            decision="Auto-cleared",
            total=extraction.total,
            reviewer_notes=None,
        )
        invoice = await self.workflow.transition(
            invoice,
            ProcessingStatus.ACTIONED,
            trigger="Upload",
            stage="External Action",
            summary=f"Created payment-ready approval packet {packet.action_id}",
            additional_updates=InvoiceUpdate(
                external_action_status="Success",
                external_action_id=packet.action_id,
            ),
        )
        return await self.workflow.transition(
            invoice,
            ProcessingStatus.COMPLETED,
            trigger="Upload",
            stage="External Action",
            summary="Clean invoice completed without a real payment",
        )


def _extraction_update(
    extraction: InvoiceExtraction, match: Any, validation: Any, invoice: InvoiceRecord
) -> InvoiceUpdate:
    vendor = match.vendor if match is not None else None
    return InvoiceUpdate(
        invoice_number=extraction.invoice_number,
        vendor_name=extraction.vendor_name,
        vendor_page_id=vendor.page_id if vendor else None,
        vendor_id=vendor.vendor_id if vendor else None,
        gstin=extraction.gstin,
        invoice_date=extraction.invoice_date,
        due_date=extraction.due_date,
        currency=extraction.currency,
        subtotal=extraction.subtotal,
        tax=extraction.tax,
        total=extraction.total,
        po_number=extraction.po_number,
        bank_account_reference=extraction.bank_account_reference,
        extraction_confidence=extraction.overall_confidence,
        validation_status=validation.status if validation.status != "Duplicate" else "Failed",
        risk_flags=validation.risk_flags,
        machine_reasoning=validation.machine_reasoning,
        last_updated=utc_now(),
    )
