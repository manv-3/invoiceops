"""Audit logging service for the InvoiceOps workflow.

Every significant event is logged as a ``RunLogEntry`` and optionally pushed to
the Notion Run Log database.
"""

from __future__ import annotations

import logging
from typing import Any

from app.models.run_log import RunLogEntry
from app.services import notion_client

logger = logging.getLogger(__name__)

# In-memory log kept for easy retrieval during development.
_entries: list[RunLogEntry] = []


async def log_event(
    invoice_id: str,
    event: str,
    detail: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> RunLogEntry:
    """Record an audit event and forward it to Notion."""
    entry = RunLogEntry(
        invoice_id=invoice_id,
        event=event,
        detail=detail,
        metadata=metadata,
    )
    _entries.append(entry)
    logger.info("RunLog [%s] %s: %s", invoice_id[:8], event, detail or "")

    try:
        await notion_client.add_run_log_entry(entry)
    except Exception:
        logger.exception("Failed to push run-log entry to Notion")

    return entry


def get_entries(invoice_id: str) -> list[RunLogEntry]:
    """Return all log entries for *invoice_id*."""
    return [e for e in _entries if e.invoice_id == invoice_id]


def clear() -> None:
    """Remove all entries.  Intended for testing."""
    _entries.clear()
