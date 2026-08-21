"""Background worker that generates approval packets for approved invoices.

The worker runs as a background asyncio task started by the application
lifespan.  It polls the in-memory store for invoices in ``APPROVED`` status and
generates exactly-once approval packets.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from app.config import get_settings
from app.models.invoice import ApprovalPacket, InvoiceRecord, InvoiceStatus
from app.services import notion_client, store
from app.services.run_log import log_event
from app.services.workflow import IllegalTransitionError, transition

logger = logging.getLogger(__name__)


async def _generate_packet(record: InvoiceRecord, approved_dir: Path) -> None:
    """Generate an approval packet for a single approved invoice."""
    if record.extraction is None:
        logger.warning("Skipping %s: no extraction data.", record.id)
        return

    data = record.extraction.data
    approval_source: str = "reviewer" if record.review_decision else "auto"

    packet = ApprovalPacket(
        invoice_id=record.id,
        vendor_name=data.vendor_name or "Unknown",
        invoice_number=data.invoice_number or "Unknown",
        total=data.total or 0,
        currency=data.currency or "USD",
        payment_details=data.payment_details,
        approval_source=approval_source,  # type: ignore[arg-type]
    )

    # Write packet to storage/approved/
    packet_path = approved_dir / f"{packet.action_id}.json"
    packet_path.write_text(
        json.dumps(packet.model_dump(mode="json"), indent=2, default=str),
        encoding="utf-8",
    )

    # Transition to ACTION_CREATED
    try:
        updated = record.model_copy(update={"action_id": packet.action_id})
        updated = transition(updated, InvoiceStatus.ACTION_CREATED)
        store.save(updated)
        await log_event(
            record.id,
            "action_created",
            f"Approval packet {packet.action_id} written to {packet_path}.",
        )
    except IllegalTransitionError as exc:
        logger.error("Transition error for %s: %s", record.id, exc)
        await log_event(record.id, "action_failed", str(exc))

    # Update Notion
    if record.notion_page_id:
        try:
            await notion_client.update_invoice_page(
                record.notion_page_id,
                {"Status": {"select": {"name": InvoiceStatus.ACTION_CREATED.value}}},
            )
        except Exception:
            logger.exception("Failed to update Notion for %s", record.id)


async def run_approval_worker() -> None:
    """Poll for approved invoices and generate approval packets."""
    settings = get_settings()
    approved_dir = settings.storage_dir / "approved"
    approved_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Approval worker started (poll every %ds).", settings.approval_poll_seconds)

    while True:
        try:
            approved = store.list_by_status(InvoiceStatus.APPROVED.value)
            for record in approved:
                await _generate_packet(record, approved_dir)
        except Exception:
            logger.exception("Approval worker error")

        await asyncio.sleep(settings.approval_poll_seconds)
