"""Audited workflow transition service."""

from __future__ import annotations

from typing import Protocol

from app.models.invoice import InvoiceRecord, InvoiceUpdate, RunLogEntry, utc_now
from app.models.workflow import ProcessingStatus, transition_status


class WorkflowNotion(Protocol):
    async def update_invoice(self, invoice_id: str, updates: InvoiceUpdate) -> InvoiceRecord: ...

    async def create_run_log(
        self, entry: RunLogEntry, invoice_page_id: str | None = None
    ) -> RunLogEntry: ...


class WorkflowService:
    def __init__(self, notion: WorkflowNotion, service_version: str = "0.1.0") -> None:
        self.notion = notion
        self.service_version = service_version

    async def transition(
        self,
        invoice: InvoiceRecord,
        target: ProcessingStatus,
        *,
        trigger: str,
        stage: str,
        summary: str,
        result: str = "Success",
        additional_updates: InvoiceUpdate | None = None,
    ) -> InvoiceRecord:
        transition_status(invoice.processing_status, target)
        updates = additional_updates or InvoiceUpdate()
        updates = updates.model_copy(
            update={"processing_status": target, "last_updated": utc_now()}
        )
        updated = await self.notion.update_invoice(
            invoice.invoice_id,
            updates,
        )
        await self.notion.create_run_log(
            RunLogEntry(
                invoice_id=invoice.invoice_id,
                trigger=trigger,
                stage=stage,
                result=result,
                summary=summary,
                service_version=self.service_version,
                invoice_page_id=updated.page_id,
            ),
            updated.page_id,
        )
        return updated
