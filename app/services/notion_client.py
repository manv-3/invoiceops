"""Notion API client for Vendors, Invoices, and Run Log databases.

When ``dev_mode`` is enabled every call is a logged no-op.  The real client
uses HTTPX to talk to the Notion data-source API.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_settings
from app.models.invoice import InvoiceRecord
from app.models.run_log import RunLogEntry

logger = logging.getLogger(__name__)

_NOTION_BASE = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


def _headers() -> dict[str, str]:
    settings = get_settings()
    token = settings.notion_token
    if token is None:
        raise RuntimeError("NOTION_TOKEN is not configured.")
    return {
        "Authorization": f"Bearer {token.get_secret_value()}",
        "Notion-Version": _NOTION_VERSION,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Dev-mode stubs
# ---------------------------------------------------------------------------


async def _dev_create_invoice_page(record: InvoiceRecord) -> str:
    logger.info("[dev] Would create Notion invoice page for %s", record.id)
    return f"dev-page-{record.id[:8]}"


async def _dev_update_invoice_page(page_id: str, properties: dict[str, Any]) -> None:
    logger.info("[dev] Would update Notion page %s with %s", page_id, list(properties))


async def _dev_add_run_log_entry(entry: RunLogEntry) -> str:
    logger.info("[dev] Would add run-log entry: %s – %s", entry.event, entry.detail)
    return f"dev-log-{entry.id[:8]}"


# ---------------------------------------------------------------------------
# Real Notion calls
# ---------------------------------------------------------------------------


async def _api_create_invoice_page(record: InvoiceRecord) -> str:
    """Create an invoice page in the Notion Invoices database."""
    settings = get_settings()
    db_id = settings.notion_invoices_data_source_id
    if not db_id:
        raise RuntimeError("NOTION_INVOICES_DATA_SOURCE_ID is not configured.")

    data = record.extraction.data if record.extraction else None
    properties: dict[str, Any] = {
        "Invoice ID": {"title": [{"text": {"content": record.id}}]},
        "Status": {"select": {"name": record.status.value}},
        "Vendor": {
            "rich_text": [
                {"text": {"content": (data.vendor_name or "Unknown") if data else "Unknown"}}
            ]
        },
        "Amount": {"number": float(data.total) if data and data.total else 0},
        "Currency": {"rich_text": [{"text": {"content": (data.currency or "") if data else ""}}]},
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_NOTION_BASE}/pages",
            headers=_headers(),
            json={"parent": {"database_id": db_id}, "properties": properties},
        )
        resp.raise_for_status()
        return resp.json()["id"]


async def _api_update_invoice_page(page_id: str, properties: dict[str, Any]) -> None:
    """Update properties on an existing Notion page."""
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{_NOTION_BASE}/pages/{page_id}",
            headers=_headers(),
            json={"properties": properties},
        )
        resp.raise_for_status()


async def _api_add_run_log_entry(entry: RunLogEntry) -> str:
    """Add a row to the Notion Run Log database."""
    settings = get_settings()
    db_id = settings.notion_run_log_data_source_id
    if not db_id:
        raise RuntimeError("NOTION_RUN_LOG_DATA_SOURCE_ID is not configured.")

    properties: dict[str, Any] = {
        "Entry ID": {"title": [{"text": {"content": entry.id}}]},
        "Invoice ID": {"rich_text": [{"text": {"content": entry.invoice_id}}]},
        "Event": {"select": {"name": entry.event}},
        "Detail": {"rich_text": [{"text": {"content": entry.detail or ""}}]},
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_NOTION_BASE}/pages",
            headers=_headers(),
            json={"parent": {"database_id": db_id}, "properties": properties},
        )
        resp.raise_for_status()
        return resp.json()["id"]


# ---------------------------------------------------------------------------
# Public interface – dispatches to dev stubs or real API
# ---------------------------------------------------------------------------


async def create_invoice_page(record: InvoiceRecord) -> str:
    """Create a Notion page for *record* and return the page ID."""
    settings = get_settings()
    if settings.dev_mode or settings.notion_token is None:
        return await _dev_create_invoice_page(record)
    return await _api_create_invoice_page(record)


async def update_invoice_page(page_id: str, properties: dict[str, Any]) -> None:
    """Update an existing Notion invoice page."""
    settings = get_settings()
    if settings.dev_mode or settings.notion_token is None:
        return await _dev_update_invoice_page(page_id, properties)
    return await _api_update_invoice_page(page_id, properties)


async def add_run_log_entry(entry: RunLogEntry) -> str:
    """Add *entry* to the Notion Run Log and return the page ID."""
    settings = get_settings()
    if settings.dev_mode or settings.notion_token is None:
        return await _dev_add_run_log_entry(entry)
    return await _api_add_run_log_entry(entry)
