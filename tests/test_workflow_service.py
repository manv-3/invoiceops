import asyncio

import pytest

from app.models.invoice import InvoiceRecord, InvoiceUpdate, RunLogEntry
from app.models.workflow import ProcessingStatus, WorkflowTransitionError
from app.services.workflow_service import WorkflowService


class FakeNotion:
    def __init__(self) -> None:
        self.updated: list[InvoiceUpdate] = []
        self.logs: list[RunLogEntry] = []

    async def update_invoice(self, invoice_id: str, updates: InvoiceUpdate) -> InvoiceRecord:
        self.updated.append(updates)
        return InvoiceRecord(
            invoice_id=invoice_id,
            page_id="page-1",
            processing_status=updates.processing_status or ProcessingStatus.RECEIVED,
        )

    async def create_run_log(
        self, entry: RunLogEntry, invoice_page_id: str | None = None
    ) -> RunLogEntry:
        self.logs.append(entry)
        return entry


def test_transition_updates_invoice_and_writes_run_log() -> None:
    notion = FakeNotion()
    service = WorkflowService(notion, service_version="0.1.0")

    result = asyncio.run(
        service.transition(
            InvoiceRecord(invoice_id="INV-1", page_id="page-1"),
            ProcessingStatus.EXTRACTING,
            trigger="Upload",
            stage="Extraction",
            summary="Started extraction",
        )
    )

    assert result.processing_status is ProcessingStatus.EXTRACTING
    assert notion.updated[0].processing_status is ProcessingStatus.EXTRACTING
    assert notion.logs[0].stage == "Extraction"


def test_invalid_transition_does_not_write() -> None:
    notion = FakeNotion()
    service = WorkflowService(notion)

    with pytest.raises(WorkflowTransitionError):
        asyncio.run(
            service.transition(
                InvoiceRecord(invoice_id="INV-1", processing_status=ProcessingStatus.COMPLETED),
                ProcessingStatus.EXTRACTING,
                trigger="Test",
                stage="Validation",
                summary="Invalid",
            )
        )
    assert notion.updated == []
    assert notion.logs == []
