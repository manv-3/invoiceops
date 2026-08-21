"""Human approval processing and exactly-once payment-ready packet generation."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from app.models.invoice import InvoiceRecord, InvoiceUpdate, RunLogEntry
from app.models.workflow import HumanDecision, ProcessingStatus
from app.services.workflow_service import WorkflowService

logger = logging.getLogger(__name__)


class OverrideNotesRequired(ValueError):
    """Raised when an Override decision has no reviewer explanation."""


class ApprovalNotEligible(ValueError):
    """Raised when a review row no longer satisfies exactly-once preconditions."""


class ApprovalNotion(Protocol):
    async def get_invoice(self, invoice_id: str) -> InvoiceRecord | None: ...

    async def update_invoice(self, invoice_id: str, updates: InvoiceUpdate) -> InvoiceRecord: ...

    async def create_run_log(
        self, entry: RunLogEntry, invoice_page_id: str | None = None
    ) -> RunLogEntry: ...


@dataclass(frozen=True)
class ActionPacket:
    action_id: str
    path: Path
    created: bool


@dataclass(frozen=True)
class ApprovalProcessResult:
    processed: bool
    action_id: str | None = None
    reason: str | None = None


class ActionPacketService:
    def __init__(self, approved_dir: Path) -> None:
        self.approved_dir = approved_dir

    def generate(
        self,
        invoice: InvoiceRecord,
        decision: HumanDecision | str,
        total: Decimal,
        reviewer_notes: str | None,
    ) -> ActionPacket:
        self.approved_dir.mkdir(parents=True, exist_ok=True)
        decision_value = decision.value if isinstance(decision, HumanDecision) else decision
        stable_input = "|".join(
            (
                invoice.invoice_id,
                decision_value,
                format(total, "f"),
                invoice.document_fingerprint or "",
            )
        )
        action_id = f"ACTION-{hashlib.sha256(stable_input.encode()).hexdigest()[:32]}"
        final_path = self.approved_dir / f"{action_id}.json"
        packet = {
            "invoice_id": invoice.invoice_id,
            "vendor": invoice.vendor_name,
            "invoice_number": invoice.invoice_number,
            "total": format(total, "f"),
            "currency": invoice.currency,
            "approval_timestamp": datetime.now(UTC).isoformat(),
            "reviewer_decision": decision_value,
            "po_number": invoice.po_number,
            "risk_flags": invoice.risk_flags,
            "reviewer_notes": reviewer_notes,
            "source_invoice_reference": invoice.source_url or invoice.document_fingerprint,
        }
        temporary = self.approved_dir / f".{action_id}-{uuid4().hex}.tmp"
        temporary.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")
        with temporary.open("rb") as file_handle:
            os.fsync(file_handle.fileno())
        try:
            os.link(temporary, final_path)
        except FileExistsError:
            temporary.unlink(missing_ok=True)
            return ActionPacket(action_id, final_path, False)
        else:
            temporary.unlink(missing_ok=True)
            return ActionPacket(action_id, final_path, True)


class ApprovalProcessor:
    def __init__(
        self, notion: ApprovalNotion, approved_dir: Path, service_version: str = "0.1.0"
    ) -> None:
        self.notion = notion
        self.workflow = WorkflowService(notion, service_version)
        self.action_packets = ActionPacketService(approved_dir)
        self._lock = asyncio.Lock()
        self._processed_invoice_ids: set[str] = set()

    async def process(
        self,
        invoice: InvoiceRecord,
        decision: HumanDecision,
        reviewer_notes: str | None = None,
        corrected_total: Decimal | None = None,
    ) -> ApprovalProcessResult:
        async with self._lock:
            if invoice.invoice_id in self._processed_invoice_ids:
                return ApprovalProcessResult(
                    False, reason="decision already processed in this worker"
                )
            get_invoice = getattr(self.notion, "get_invoice", None)
            if get_invoice is not None:
                refreshed = await get_invoice(invoice.invoice_id)
                if refreshed is None:
                    raise ApprovalNotEligible("invoice no longer exists")
                invoice = refreshed
                if invoice.human_decision is not decision:
                    raise ApprovalNotEligible("human decision changed during approval processing")
            self._assert_eligible(invoice, decision, reviewer_notes)
            now = datetime.now(UTC)
            if decision is HumanDecision.REJECT:
                await self.workflow.transition(
                    invoice,
                    ProcessingStatus.REJECTED,
                    trigger="Approval Change",
                    stage="Approval",
                    summary="Reviewer rejected the invoice",
                    additional_updates=InvoiceUpdate(
                        human_decision=decision,
                        reviewer_notes=reviewer_notes,
                        reviewed_at=now,
                        decision_processed_at=now,
                        last_updated=now,
                    ),
                )
                self._processed_invoice_ids.add(invoice.invoice_id)
                return ApprovalProcessResult(True, reason="rejected")

            effective_total = (
                corrected_total
                if corrected_total is not None
                else invoice.corrected_total
                if invoice.corrected_total is not None
                else invoice.total
            )
            if effective_total is None:
                raise ApprovalNotEligible("approved invoice has no total")
            if decision is HumanDecision.OVERRIDE:
                self._validate_override_total(invoice, effective_total)
            current = await self.workflow.transition(
                invoice,
                ProcessingStatus.APPROVED,
                trigger="Approval Change",
                stage="Approval",
                summary=(
                    "Reviewer approved the invoice"
                    if decision is HumanDecision.APPROVE
                    else "Reviewer overrode the invoice with a note"
                ),
                additional_updates=InvoiceUpdate(
                    human_decision=decision,
                    corrected_total=corrected_total,
                    reviewer_notes=reviewer_notes,
                    reviewed_at=now,
                ),
            )
            try:
                packet = self.action_packets.generate(
                    current, decision, effective_total, reviewer_notes
                )
                current = await self.workflow.transition(
                    current,
                    ProcessingStatus.ACTIONED,
                    trigger="Approval Change",
                    stage="External Action",
                    summary=f"Created payment-ready approval packet {packet.action_id}",
                    additional_updates=InvoiceUpdate(
                        external_action_status="Success",
                        external_action_id=packet.action_id,
                        approved_at=now,
                    ),
                )
                # This is deliberately the final write carrying the completion marker.
                current = await self.workflow.transition(
                    current,
                    ProcessingStatus.COMPLETED,
                    trigger="Approval Change",
                    stage="External Action",
                    summary="External action packet processing completed",
                    additional_updates=InvoiceUpdate(decision_processed_at=datetime.now(UTC)),
                )
            except Exception as exc:
                await self._handle_processing_failure(current, exc)
                raise
            self._processed_invoice_ids.add(invoice.invoice_id)
            return ApprovalProcessResult(True, action_id=packet.action_id)

    async def handle_poll_failure(
        self, invoice: InvoiceRecord, error: Exception, retry_count: int
    ) -> None:
        """Persist a bounded poll failure and terminally fail after the retry limit."""

        current = await self._refresh_invoice(invoice)
        await self._log_retry_failure(current, error, retry_count)
        if retry_count >= 3:
            await self._mark_failed(current, error, "Approval retry limit reached")

    async def _handle_processing_failure(self, invoice: InvoiceRecord, error: Exception) -> None:
        """Make partially advanced approval state visible instead of leaving it stranded."""

        current = await self._refresh_invoice(invoice)
        if current.processing_status in {
            ProcessingStatus.APPROVED,
            ProcessingStatus.ACTIONED,
        }:
            await self._mark_failed(current, error, "Approval packet processing failed")
        else:
            await self._log_action_failure(current, error)

    async def _mark_failed(self, invoice: InvoiceRecord, error: Exception, summary: str) -> None:
        updates = {"last_updated": datetime.now(UTC)}
        if invoice.external_action_id or invoice.external_action_status == "Success":
            updates["external_action_status"] = "Failed"
        try:
            await self.workflow.transition(
                invoice,
                ProcessingStatus.FAILED,
                trigger="Approval Change",
                stage="External Action",
                result="Failed",
                summary=summary,
                additional_updates=InvoiceUpdate.model_validate(updates),
            )
        except Exception as failure_error:
            logger.exception(
                "could not mark invoice %s as Failed after approval processing error",
                invoice.invoice_id,
            )
            await self._log_action_failure(invoice, failure_error)

    async def _refresh_invoice(self, invoice: InvoiceRecord) -> InvoiceRecord:
        get_invoice = getattr(self.notion, "get_invoice", None)
        if get_invoice is None:
            return invoice
        try:
            refreshed = await get_invoice(invoice.invoice_id)
        except Exception:
            logger.exception(
                "could not refresh invoice %s after approval failure", invoice.invoice_id
            )
            return invoice
        return refreshed or invoice

    async def _log_retry_failure(
        self, invoice: InvoiceRecord, error: Exception, retry_count: int
    ) -> None:
        try:
            await self.notion.create_run_log(
                RunLogEntry(
                    invoice_id=invoice.invoice_id,
                    invoice_page_id=invoice.page_id,
                    trigger="Approval Poller",
                    stage="Retry",
                    result="Failed",
                    summary="Approval processing attempt failed",
                    error_message=f"{type(error).__name__}: {str(error)[:240]}",
                    retry_count=retry_count,
                    service_version=self.workflow.service_version,
                ),
                invoice.page_id,
            )
        except Exception:
            # The original processing error remains visible to the poller; a secondary
            # logging failure must not stop unrelated review rows.
            logger.exception("could not write approval retry log for %s", invoice.invoice_id)
            return

    async def _log_action_failure(self, invoice: InvoiceRecord, error: Exception) -> None:
        try:
            await self.notion.create_run_log(
                RunLogEntry(
                    invoice_id=invoice.invoice_id,
                    invoice_page_id=invoice.page_id,
                    trigger="Approval Change",
                    stage="External Action",
                    result="Failed",
                    summary="Payment-ready approval packet processing failed",
                    error_message=f"{type(error).__name__}: {str(error)[:240]}",
                    service_version=self.workflow.service_version,
                ),
                invoice.page_id,
            )
        except Exception:
            # Preserve the original action error when the secondary audit write fails.
            logger.exception("could not write approval failure log for %s", invoice.invoice_id)
            return

    @staticmethod
    def _assert_eligible(
        invoice: InvoiceRecord,
        decision: HumanDecision,
        reviewer_notes: str | None,
    ) -> None:
        if invoice.processing_status is not ProcessingStatus.NEEDS_REVIEW:
            raise ApprovalNotEligible("invoice is not in Needs Review")
        if decision is HumanDecision.PENDING:
            raise ApprovalNotEligible("human decision is still Pending")
        if invoice.decision_processed_at is not None:
            raise ApprovalNotEligible("decision has already been processed")
        if invoice.external_action_id:
            raise ApprovalNotEligible("external action already has an ID")
        if invoice.external_action_status == "Success":
            raise ApprovalNotEligible("external action already succeeded")
        if decision is HumanDecision.OVERRIDE and not (reviewer_notes or "").strip():
            raise OverrideNotesRequired("Override requires reviewer notes")

    @staticmethod
    def _validate_override_total(invoice: InvoiceRecord, corrected_total: Decimal) -> None:
        if invoice.subtotal is None or invoice.tax is None:
            return
        difference = abs(invoice.subtotal + invoice.tax - corrected_total)
        if difference > Decimal("0.01"):
            raise ApprovalNotEligible("corrected total fails the deterministic arithmetic check")
