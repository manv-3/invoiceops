import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.models.invoice import InvoiceRecord, InvoiceUpdate, RunLogEntry
from app.models.workflow import HumanDecision, ProcessingStatus
from app.services.action_service import ApprovalProcessor, OverrideNotesRequired


class FakeNotion:
    def __init__(self) -> None:
        self.invoice = InvoiceRecord(
            invoice_id="INV-1",
            page_id="page-1",
            vendor_name="Acme",
            invoice_number="A-1",
            total=Decimal("11.80"),
            currency="INR",
            processing_status=ProcessingStatus.NEEDS_REVIEW,
            human_decision=HumanDecision.PENDING,
            document_fingerprint="a" * 64,
        )
        self.logs: list[RunLogEntry] = []
        self.updates: list[InvoiceUpdate] = []

    async def get_invoice(self, invoice_id: str) -> InvoiceRecord | None:
        return self.invoice if invoice_id == self.invoice.invoice_id else None

    async def update_invoice(self, invoice_id: str, updates: InvoiceUpdate) -> InvoiceRecord:
        self.updates.append(updates)
        values = updates.model_dump(exclude_unset=True)
        self.invoice = self.invoice.model_copy(update=values)
        return self.invoice

    async def create_run_log(
        self, entry: RunLogEntry, invoice_page_id: str | None = None
    ) -> RunLogEntry:
        self.logs.append(entry)
        return entry


class FailingActionNotion(FakeNotion):
    async def update_invoice(self, invoice_id: str, updates: InvoiceUpdate) -> InvoiceRecord:
        if "external_action_status" in updates.model_fields_set:
            raise RuntimeError("temporary action persistence failure")
        return await super().update_invoice(invoice_id, updates)


class FailingCompletedTransitionNotion(FakeNotion):
    async def update_invoice(self, invoice_id: str, updates: InvoiceUpdate) -> InvoiceRecord:
        if updates.processing_status is ProcessingStatus.COMPLETED:
            raise RuntimeError("temporary completion transition failure")
        return await super().update_invoice(invoice_id, updates)


def test_approve_creates_one_packet_and_repeated_processing_is_idempotent(tmp_path: Path) -> None:
    notion = FakeNotion()
    notion.invoice = notion.invoice.model_copy(update={"human_decision": HumanDecision.APPROVE})
    processor = ApprovalProcessor(notion, tmp_path, service_version="0.1.0")

    async def run():
        first = await processor.process(notion.invoice, HumanDecision.APPROVE)
        second = await processor.process(notion.invoice, HumanDecision.APPROVE)
        return first, second

    first, second = asyncio.run(run())
    assert first.processed is True
    assert second.processed is False
    assert first.action_id is not None
    assert len(list(tmp_path.glob("*.json"))) == 1
    packet = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert packet["invoice_id"] == "INV-1"
    assert packet["total"] == "11.80"
    assert notion.invoice.processing_status is ProcessingStatus.COMPLETED
    assert notion.invoice.external_action_id == first.action_id


def test_reject_does_not_create_action_packet(tmp_path: Path) -> None:
    notion = FakeNotion()
    notion.invoice = notion.invoice.model_copy(update={"human_decision": HumanDecision.REJECT})
    processor = ApprovalProcessor(notion, tmp_path)

    result = asyncio.run(
        processor.process(notion.invoice, HumanDecision.REJECT, reviewer_notes="Not valid")
    )

    assert result.processed is True
    assert notion.invoice.processing_status is ProcessingStatus.REJECTED
    assert list(tmp_path.glob("*.json")) == []
    assert notion.invoice.external_action_id is None


def test_override_requires_reviewer_notes(tmp_path: Path) -> None:
    notion = FakeNotion()
    notion.invoice = notion.invoice.model_copy(update={"human_decision": HumanDecision.OVERRIDE})
    processor = ApprovalProcessor(notion, tmp_path)

    with pytest.raises(OverrideNotesRequired):
        asyncio.run(
            processor.process(notion.invoice, HumanDecision.OVERRIDE, corrected_total=Decimal("10"))
        )


def test_override_rejects_arithmetically_invalid_corrected_total(tmp_path: Path) -> None:
    notion = FakeNotion()
    notion.invoice = notion.invoice.model_copy(
        update={
            "subtotal": Decimal("10.00"),
            "tax": Decimal("1.80"),
            "human_decision": HumanDecision.OVERRIDE,
        }
    )
    processor = ApprovalProcessor(notion, tmp_path)

    with pytest.raises(ValueError, match="arithmetic"):
        asyncio.run(
            processor.process(
                notion.invoice,
                HumanDecision.OVERRIDE,
                corrected_total=Decimal("15.00"),
                reviewer_notes="Corrected after reviewer checked source",
            )
        )


def test_zero_total_is_not_treated_as_missing(tmp_path: Path) -> None:
    notion = FakeNotion()
    notion.invoice = notion.invoice.model_copy(
        update={"total": Decimal("0.00"), "human_decision": HumanDecision.APPROVE}
    )
    processor = ApprovalProcessor(notion, tmp_path)

    result = asyncio.run(processor.process(notion.invoice, HumanDecision.APPROVE))

    assert result.processed is True
    assert result.action_id is not None


def test_failed_action_persists_failure_log_and_marks_partial_approval_failed(
    tmp_path: Path,
) -> None:
    notion = FailingActionNotion()
    notion.invoice = notion.invoice.model_copy(update={"human_decision": HumanDecision.APPROVE})
    processor = ApprovalProcessor(notion, tmp_path)

    with pytest.raises(RuntimeError, match="persistence"):
        asyncio.run(processor.process(notion.invoice, HumanDecision.APPROVE))

    assert notion.invoice.processing_status is ProcessingStatus.FAILED
    assert notion.invoice.external_action_id is None
    assert any(log.result == "Failed" for log in notion.logs)
    assert len(list(tmp_path.glob("*.json"))) == 1


def test_decision_timestamp_is_written_only_with_completed_transition(tmp_path: Path) -> None:
    notion = FakeNotion()
    notion.invoice = notion.invoice.model_copy(update={"human_decision": HumanDecision.APPROVE})
    processor = ApprovalProcessor(notion, tmp_path)

    asyncio.run(processor.process(notion.invoice, HumanDecision.APPROVE))

    status_updates = [
        update.processing_status
        for update in notion.updates
        if update.processing_status is not None
    ]
    assert status_updates == [
        ProcessingStatus.APPROVED,
        ProcessingStatus.ACTIONED,
        ProcessingStatus.COMPLETED,
    ]
    assert all(
        update.decision_processed_at is None
        for update in notion.updates[:-1]
        if update.processing_status is not ProcessingStatus.REJECTED
    )
    assert notion.updates[-1].decision_processed_at is not None


def test_failed_final_transition_is_marked_failed_and_not_falsely_completed(
    tmp_path: Path,
) -> None:
    notion = FailingCompletedTransitionNotion()
    notion.invoice = notion.invoice.model_copy(update={"human_decision": HumanDecision.APPROVE})
    processor = ApprovalProcessor(notion, tmp_path)

    with pytest.raises(RuntimeError, match="completion"):
        asyncio.run(processor.process(notion.invoice, HumanDecision.APPROVE))

    assert notion.invoice.processing_status is ProcessingStatus.FAILED
    assert notion.invoice.decision_processed_at is None
    assert notion.invoice.external_action_status == "Failed"
    assert notion.invoice.external_action_id is not None
