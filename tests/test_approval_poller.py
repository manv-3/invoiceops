import asyncio
from pathlib import Path

from app.models.invoice import InvoiceRecord
from app.models.workflow import HumanDecision, ProcessingStatus
from app.workers.approval_poller import ApprovalPoller


class FakeNotion:
    async def query_pending_reviews(self) -> list[InvoiceRecord]:
        return [
            InvoiceRecord(
                invoice_id="INV-1",
                processing_status=ProcessingStatus.NEEDS_REVIEW,
                human_decision=HumanDecision.APPROVE,
            )
        ]


class FakeProcessor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.failures: list[tuple[str, int]] = []
        self.always_fail: set[str] = set()

    async def process(self, invoice, decision, reviewer_notes=None, corrected_total=None):
        self.calls.append(invoice.invoice_id)
        if invoice.invoice_id in self.always_fail:
            raise RuntimeError("permanent test failure")

    async def handle_poll_failure(self, invoice, error, retry_count):
        self.failures.append((invoice.invoice_id, retry_count))


def test_poller_processes_only_rows_returned_by_review_query() -> None:
    notion = FakeNotion()
    processor = FakeProcessor()
    poller = ApprovalPoller(notion, processor, Path("storage/test"), poll_seconds=1)

    asyncio.run(poller.run_once())

    assert processor.calls == ["INV-1"]


def test_one_failed_invoice_does_not_block_other_approvals() -> None:
    class MultipleNotion:
        async def query_pending_reviews(self) -> list[InvoiceRecord]:
            return [
                InvoiceRecord(
                    invoice_id="INV-A",
                    processing_status=ProcessingStatus.NEEDS_REVIEW,
                    human_decision=HumanDecision.APPROVE,
                ),
                InvoiceRecord(
                    invoice_id="INV-B",
                    processing_status=ProcessingStatus.NEEDS_REVIEW,
                    human_decision=HumanDecision.APPROVE,
                ),
            ]

    processor = FakeProcessor()
    processor.always_fail.add("INV-A")
    poller = ApprovalPoller(MultipleNotion(), processor, Path("storage/test"), poll_seconds=1)

    processed = asyncio.run(poller.run_once())

    assert processed == 1
    assert processor.calls == ["INV-A", "INV-B"]
    assert processor.failures == [("INV-A", 1)]


def test_poller_stops_after_three_failures_for_one_invoice() -> None:
    processor = FakeProcessor()
    processor.always_fail.add("INV-1")
    poller = ApprovalPoller(
        FakeNotion(), processor, Path("storage/test"), poll_seconds=1, max_retries=3
    )

    for _ in range(4):
        asyncio.run(poller.run_once())

    assert processor.calls == ["INV-1", "INV-1", "INV-1"]
    assert processor.failures == [("INV-1", 1), ("INV-1", 2), ("INV-1", 3)]
