"""Async Notion review poller for human decisions."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from app.models.invoice import InvoiceRecord
from app.models.workflow import HumanDecision

logger = logging.getLogger(__name__)


class ReviewQuery(Protocol):
    async def query_pending_reviews(self) -> list[InvoiceRecord]: ...


class ReviewProcessor(Protocol):
    async def process(
        self,
        invoice: InvoiceRecord,
        decision: HumanDecision,
        reviewer_notes: str | None = None,
        corrected_total: Decimal | None = None,
    ) -> object: ...

    async def handle_poll_failure(
        self, invoice: InvoiceRecord, error: Exception, retry_count: int
    ) -> None: ...


class ApprovalPoller:
    def __init__(
        self,
        notion: ReviewQuery,
        processor: ReviewProcessor,
        approved_dir: Path,
        poll_seconds: int = 10,
        max_retries: int = 3,
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        self.notion = notion
        self.processor = processor
        self.approved_dir = approved_dir
        self.poll_seconds = poll_seconds
        self.max_retries = max_retries
        self._retry_counts: dict[str, int] = {}

    async def run_once(self) -> int:
        invoices = await self.notion.query_pending_reviews()
        processed = 0
        for invoice in invoices:
            if invoice.human_decision is HumanDecision.PENDING:
                continue
            retry_count = await self._retry_count(invoice)
            if retry_count >= self.max_retries:
                continue
            try:
                await self.processor.process(
                    invoice,
                    invoice.human_decision,
                    reviewer_notes=invoice.reviewer_notes,
                    corrected_total=invoice.corrected_total,
                )
            except Exception as exc:
                retry_count += 1
                self._retry_counts[invoice.invoice_id] = retry_count
                try:
                    await self.processor.handle_poll_failure(invoice, exc, retry_count)
                except Exception:
                    logger.exception(
                        "failed to persist approval failure for invoice %s",
                        invoice.invoice_id,
                    )
                continue
            self._retry_counts.pop(invoice.invoice_id, None)
            processed += 1
        return processed

    async def _retry_count(self, invoice: InvoiceRecord) -> int:
        in_memory = self._retry_counts.get(invoice.invoice_id, 0)
        get_retry_count = getattr(self.notion, "get_retry_count", None)
        if get_retry_count is None or invoice.page_id is None:
            return in_memory
        try:
            durable = await get_retry_count(invoice.page_id)
        except Exception:
            logger.exception("could not read retry count for invoice %s", invoice.invoice_id)
            return in_memory
        return max(in_memory, durable)

    async def run_forever(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self.run_once()
            except Exception:
                # The next bounded poll retries provider/transient failures. Individual
                # invoice failures are handled by ApprovalProcessor and remain auditable.
                logger.exception("approval poll failed")
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                continue

    async def stop(self, task: asyncio.Task[None] | None) -> None:
        if task is None:
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
