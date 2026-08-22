"""Notion data-source client and application-to-Notion property mappings.

All Notion HTTP details live here. The rest of the application deals only in typed
InvoiceOps models and never depends on raw Notion JSON.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from app.models.invoice import (
    DuplicateMatch,
    InvoiceRecord,
    InvoiceUpdate,
    RunLogEntry,
    VendorRecord,
    utc_now,
)
from app.models.workflow import HumanDecision, ProcessingStatus

NOTION_API_BASE_URL = "https://api.notion.com/v1"
NOTION_API_VERSION = "2026-03-11"


class NotionApiError(RuntimeError):
    """Safe error raised for failed Notion requests."""

    def __init__(self, status_code: int, method: str, path: str, message: str) -> None:
        super().__init__(f"Notion {method} {path} failed with HTTP {status_code}: {message}")
        self.status_code = status_code
        self.method = method
        self.path = path


class NotionRecordNotFound(LookupError):
    """Raised when an update requires a missing Notion row."""


class NotionClient:
    """Typed async client for the InvoiceOps Notion data sources."""

    def __init__(
        self,
        token: str,
        invoices_data_source_id: str,
        vendors_data_source_id: str,
        run_log_data_source_id: str,
        api_version: str = NOTION_API_VERSION,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.invoices_data_source_id = invoices_data_source_id
        self.vendors_data_source_id = vendors_data_source_id
        self.run_log_data_source_id = run_log_data_source_id
        self._http_client = http_client or httpx.AsyncClient(
            base_url=NOTION_API_BASE_URL,
            timeout=httpx.Timeout(30.0),
            verify=False,
        )
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": api_version,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    async def close(self) -> None:
        await self._http_client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        retryable_statuses = {429, 500, 502, 503, 504}
        retry_transient = kwargs.pop(
            "_retry_transient", method in {"GET", "PATCH"} or "/data_sources/" in path
        )
        response: httpx.Response | None = None
        for attempt in range(3):
            try:
                response = await self._http_client.request(
                    method, path, headers=self._headers, **kwargs
                )
            except (httpx.NetworkError, httpx.TimeoutException) as exc:
                if attempt == 2:
                    raise NotionApiError(503, method, path, "transient request failed") from exc
                await asyncio.sleep(0.1 * (2**attempt))
                continue
            if (
                not retry_transient
                or response.status_code not in retryable_statuses
                or attempt == 2
            ):
                break
            retry_after = response.headers.get("Retry-After")
            try:
                delay = min(float(retry_after), 2.0) if retry_after else 0.1 * (2**attempt)
            except ValueError:
                delay = 0.1 * (2**attempt)
            await asyncio.sleep(max(delay, 0.0))

        if response is None:
            raise NotionApiError(503, method, path, "request did not return a response")
        if response.is_error:
            message = "request rejected"
            try:
                body = response.json()
                if isinstance(body, Mapping) and isinstance(body.get("message"), str):
                    message = body["message"]
            except ValueError:
                pass
            raise NotionApiError(response.status_code, method, path, message[:300])
        body = response.json()
        if not isinstance(body, dict):
            raise NotionApiError(response.status_code, method, path, "response was not an object")
        return body

    async def _query(
        self, data_source_id: str, filter_body: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            body: dict[str, Any] = {"page_size": 100}
            if filter_body:
                body["filter"] = filter_body
            if cursor:
                body["start_cursor"] = cursor
            result = await self._request("POST", f"/data_sources/{data_source_id}/query", json=body)
            page_rows = result.get("results", [])
            rows.extend(row for row in page_rows if isinstance(row, dict))
            if result.get("has_more") is not True:
                break
            next_cursor = result.get("next_cursor")
            if not isinstance(next_cursor, str) or not next_cursor:
                break
            cursor = next_cursor
        return rows

    async def create_invoice(self, invoice: InvoiceRecord) -> InvoiceRecord:
        body = {
            "parent": {"data_source_id": self.invoices_data_source_id},
            "properties": _invoice_properties(invoice),
        }
        page = await self._request("POST", "/pages", json=body)
        return _invoice_from_page(page)

    async def attach_invoice_file(self, invoice_id: str, file_path: Path) -> InvoiceRecord:
        """Upload the generated local document and attach it to Source File."""

        current = await self.get_invoice(invoice_id)
        if current is None or current.page_id is None:
            raise NotionRecordNotFound(f"invoice {invoice_id} was not found")
        content_type = {
            ".pdf": "application/pdf",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
        }.get(file_path.suffix.casefold())
        if content_type is None:
            raise ValueError("unsupported source file extension")
        upload = await self._request(
            "POST",
            "/file_uploads",
            _retry_transient=False,
            json={
                "mode": "single_part",
                "filename": file_path.name,
                "content_type": content_type,
            },
        )
        upload_id = upload.get("id")
        upload_url = upload.get("upload_url")
        if not isinstance(upload_id, str) or not isinstance(upload_url, str):
            raise NotionApiError(502, "POST", "/file_uploads", "upload response was incomplete")
        await self._send_file_upload(upload_url, file_path, content_type)
        page = await self._request(
            "PATCH",
            f"/pages/{current.page_id}",
            json={
                "properties": {
                    "Source File": {
                        "files": [
                            {
                                "type": "file_upload",
                                "file_upload": {"id": upload_id},
                                "name": file_path.name,
                            }
                        ]
                    }
                }
            },
        )
        return _invoice_from_page(page)

    async def _send_file_upload(self, upload_url: str, file_path: Path, content_type: str) -> None:
        headers = {
            "Authorization": self._headers["Authorization"],
            "Notion-Version": self._headers["Notion-Version"],
            "Accept": "application/json",
        }
        try:
            with file_path.open("rb") as source:
                response = await self._http_client.post(
                    upload_url,
                    headers=headers,
                    files={"file": (file_path.name, source, content_type)},
                )
        except (httpx.NetworkError, httpx.TimeoutException) as exc:
            raise NotionApiError(503, "POST", upload_url, "file upload request failed") from exc
        if response.is_error:
            message = "file upload rejected"
            try:
                body = response.json()
                if isinstance(body, Mapping) and isinstance(body.get("message"), str):
                    message = body["message"]
            except ValueError:
                pass
            raise NotionApiError(response.status_code, "POST", upload_url, message[:300])

    async def update_invoice(self, invoice_id: str, updates: InvoiceUpdate) -> InvoiceRecord:
        current = await self.get_invoice(invoice_id)
        if current is None or current.page_id is None:
            raise NotionRecordNotFound(f"invoice {invoice_id} was not found")
        page = await self._request(
            "PATCH",
            f"/pages/{current.page_id}",
            json={"properties": _invoice_update_properties(updates)},
        )
        return _invoice_from_page(page)

    async def get_invoice(self, invoice_id: str) -> InvoiceRecord | None:
        rows = await self._query(
            self.invoices_data_source_id,
            {"property": "Invoice ID", "rich_text": {"equals": invoice_id}},
        )
        return _invoice_from_page(rows[0]) if rows else None

    async def find_duplicate(
        self,
        document_fingerprint: str | None,
        vendor_name: str | None,
        invoice_number: str | None,
        exclude_invoice_id: str | None = None,
    ) -> DuplicateMatch | None:
        if document_fingerprint:
            rows = await self._query(
                self.invoices_data_source_id,
                {
                    "property": "Document Fingerprint",
                    "rich_text": {"equals": document_fingerprint},
                },
            )
            if rows:
                for row in rows:
                    record = _invoice_from_page(row)
                    if record.invoice_id != exclude_invoice_id:
                        return DuplicateMatch(
                            invoice_id=record.invoice_id,
                            page_id=record.page_id,
                            reason="Document fingerprint already exists",
                        )

        if not vendor_name or not invoice_number:
            return None
        vendor = await self.find_vendor(vendor_name)
        if vendor is None or vendor.page_id is None:
            return None
        rows = await self._query(
            self.invoices_data_source_id,
            {
                "and": [
                    {"property": "Invoice Number", "rich_text": {"equals": invoice_number}},
                    {"property": "Vendor", "relation": {"contains": vendor.page_id}},
                ]
            },
        )
        for row in rows:
            record = _invoice_from_page(row)
            if record.invoice_id != exclude_invoice_id:
                return DuplicateMatch(
                    invoice_id=record.invoice_id,
                    page_id=record.page_id,
                    reason="Vendor and invoice number already exist",
                )
        return None

    async def find_invoice_duplicate(
        self,
        document_fingerprint: str | None,
        vendor_name: str | None,
        invoice_number: str | None,
        exclude_invoice_id: str | None = None,
    ) -> DuplicateMatch | None:
        """Compatibility spelling used by the workflow service."""

        return await self.find_duplicate(
            document_fingerprint, vendor_name, invoice_number, exclude_invoice_id
        )

    async def find_vendor(
        self, vendor_name: str | None, gstin: str | None = None
    ) -> VendorRecord | None:
        if gstin:
            rows = await self._query(
                self.vendors_data_source_id,
                {"property": "GSTIN", "rich_text": {"equals": gstin}},
            )
            if rows:
                return _vendor_from_page(rows[0])
        if not vendor_name:
            return None
        rows = await self._query(
            self.vendors_data_source_id,
            {"property": "Vendor Name", "title": {"equals": vendor_name}},
        )
        if rows:
            return _vendor_from_page(rows[0])
        normalized_name = _normalize_vendor_name(vendor_name)
        for row in await self._query(self.vendors_data_source_id):
            candidate = _vendor_from_page(row)
            if _normalize_vendor_name(candidate.vendor_name) == normalized_name:
                return candidate
        return None

    async def create_vendor(self, vendor: VendorRecord) -> VendorRecord:
        properties: dict[str, Any] = {
            "Vendor": _title(vendor.vendor_name),
            "Vendor Name": _rich_text(vendor.vendor_name),
            "Vendor ID": _rich_text(vendor.vendor_id),
            "Active Status": _select(vendor.active_status),
            "Trusted Vendor": {"checkbox": vendor.trusted_vendor},
        }
        _put_optional(properties, "GSTIN", _rich_text(vendor.gstin), vendor.gstin)
        _put_optional(properties, "Email", {"email": vendor.email}, vendor.email)
        _put_optional(properties, "Phone", {"phone_number": vendor.phone}, vendor.phone)
        _put_optional(
            properties,
            "Approved Bank Account Reference",
            _rich_text(vendor.approved_bank_account_reference),
            vendor.approved_bank_account_reference,
        )
        _put_optional(
            properties, "Payment Terms", _select(vendor.payment_terms), vendor.payment_terms
        )
        _put_optional(properties, "Risk Notes", _rich_text(vendor.risk_notes), vendor.risk_notes)
        page = await self._request(
            "POST",
            "/pages",
            json={
                "parent": {"data_source_id": self.vendors_data_source_id},
                "properties": properties,
            },
        )
        return _vendor_from_page(page)

    async def query_pending_reviews(self) -> list[InvoiceRecord]:
        rows = await self._query(
            self.invoices_data_source_id,
            {
                "and": [
                    {"property": "Processing Status", "status": {"equals": "Needs Review"}},
                    {"property": "Human Decision", "select": {"does_not_equal": "Pending"}},
                    {"property": "Decision Processed At", "date": {"is_empty": True}},
                ]
            },
        )
        return [_invoice_from_page(row) for row in rows]

    async def get_retry_count(self, invoice_page_id: str) -> int:
        """Read the highest persisted approval retry count for one invoice."""

        rows = await self._query(
            self.run_log_data_source_id,
            {
                "and": [
                    {"property": "Invoice", "relation": {"contains": invoice_page_id}},
                    {"property": "Stage", "select": {"equals": "Retry"}},
                ]
            },
        )
        counts = [
            _decimal_value(row.get("properties", {}).get("Retry Count"))
            for row in rows
            if isinstance(row, Mapping)
        ]
        return max((int(value) for value in counts if value is not None), default=0)

    async def create_run_log(
        self, entry: RunLogEntry, invoice_page_id: str | None = None
    ) -> RunLogEntry:
        page_id = invoice_page_id or entry.invoice_page_id
        properties: dict[str, Any] = {
            "Run": _title(entry.run_id),
            "Run ID": _rich_text(entry.run_id),
            "Timestamp": _date(entry.timestamp),
            "Trigger": _select(entry.trigger),
            "Stage": _select(entry.stage),
            "Result": _select(entry.result),
            "Summary": _rich_text(entry.summary),
            "Retry Count": {"number": entry.retry_count},
            "Service Version": _rich_text(entry.service_version),
        }
        if page_id:
            properties["Invoice"] = _relation(page_id)
        _put_optional(
            properties, "Error Message", _rich_text(entry.error_message), entry.error_message
        )
        _put_optional(
            properties, "External Action", _rich_text(entry.external_action), entry.external_action
        )
        if entry.duration_ms is not None:
            properties["Duration"] = {"number": entry.duration_ms}
        page = await self._request(
            "POST",
            "/pages",
            json={
                "parent": {"data_source_id": self.run_log_data_source_id},
                "properties": properties,
            },
        )
        return _run_log_from_page(page, entry)


def _invoice_properties(invoice: InvoiceRecord) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "Invoice": _title(invoice.invoice_id),
        "Invoice ID": _rich_text(invoice.invoice_id),
        "Processing Status": _status(_enum_value(invoice.processing_status)),
        "Human Decision": _select(_enum_value(invoice.human_decision)),
        "Validation Status": _select(invoice.validation_status),
        "External Action Status": _select(invoice.external_action_status),
        "Source": _select(invoice.source),
        "Received At": _date(invoice.received_at),
        "Last Updated": _date(invoice.last_updated),
    }
    values = {
        "Invoice Number": _rich_text(invoice.invoice_number),
        "Vendor": _relation(invoice.vendor_page_id),
        "GSTIN": _rich_text(invoice.gstin),
        "Invoice Date": _date(invoice.invoice_date),
        "Due Date": _date(invoice.due_date),
        "Currency": _select(invoice.currency),
        "Subtotal": _number(invoice.subtotal),
        "Tax": _number(invoice.tax),
        "Total": _number(invoice.total),
        "Corrected Total": _number(invoice.corrected_total),
        "PO Number": _rich_text(invoice.po_number),
        "Source URL": {"url": invoice.source_url},
        "Extraction Confidence": _number(invoice.extraction_confidence),
        "Risk Flags": _multi_select(invoice.risk_flags),
        "Reviewer Notes": _rich_text(invoice.reviewer_notes),
        "Machine Reasoning": _rich_text(invoice.machine_reasoning),
        "Document Fingerprint": _rich_text(invoice.document_fingerprint),
        "Extracted Bank Account Reference": _rich_text(invoice.bank_account_reference),
        "External Action ID": _rich_text(invoice.external_action_id),
        "Decision Processed At": _date(invoice.decision_processed_at),
        "Reviewed At": _date(invoice.reviewed_at),
        "Approved At": _date(invoice.approved_at),
    }
    for name, value in values.items():
        if value is not None:
            properties[name] = value
    return properties


def _invoice_update_properties(updates: InvoiceUpdate) -> dict[str, Any]:
    mapping: dict[str, tuple[str, Any]] = {
        "invoice_number": ("Invoice Number", _rich_text(updates.invoice_number)),
        "vendor_page_id": ("Vendor", _relation(updates.vendor_page_id)),
        "gstin": ("GSTIN", _rich_text(updates.gstin)),
        "invoice_date": ("Invoice Date", _date(updates.invoice_date)),
        "due_date": ("Due Date", _date(updates.due_date)),
        "currency": ("Currency", _select(updates.currency)),
        "subtotal": ("Subtotal", _number(updates.subtotal)),
        "tax": ("Tax", _number(updates.tax)),
        "total": ("Total", _number(updates.total)),
        "corrected_total": ("Corrected Total", _number(updates.corrected_total)),
        "po_number": ("PO Number", _rich_text(updates.po_number)),
        "source": ("Source", _select(updates.source)),
        "source_url": ("Source URL", {"url": updates.source_url}),
        "extraction_confidence": ("Extraction Confidence", _number(updates.extraction_confidence)),
        "processing_status": ("Processing Status", _status(_enum_value(updates.processing_status))),
        "human_decision": ("Human Decision", _select(_enum_value(updates.human_decision))),
        "validation_status": ("Validation Status", _select(updates.validation_status)),
        "risk_flags": ("Risk Flags", _multi_select(updates.risk_flags)),
        "machine_reasoning": ("Machine Reasoning", _rich_text(updates.machine_reasoning)),
        "reviewer_notes": ("Reviewer Notes", _rich_text(updates.reviewer_notes)),
        "external_action_status": (
            "External Action Status",
            _select(updates.external_action_status),
        ),
        "external_action_id": ("External Action ID", _rich_text(updates.external_action_id)),
        "document_fingerprint": ("Document Fingerprint", _rich_text(updates.document_fingerprint)),
        "bank_account_reference": (
            "Extracted Bank Account Reference",
            _rich_text(updates.bank_account_reference),
        ),
        "decision_processed_at": ("Decision Processed At", _date(updates.decision_processed_at)),
        "received_at": ("Received At", _date(updates.received_at)),
        "reviewed_at": ("Reviewed At", _date(updates.reviewed_at)),
        "approved_at": ("Approved At", _date(updates.approved_at)),
        "last_updated": ("Last Updated", _date(updates.last_updated)),
    }
    return {
        notion_name: value
        for field_name, (notion_name, value) in mapping.items()
        if field_name in updates.model_fields_set
    }


def _invoice_from_page(page: Mapping[str, Any]) -> InvoiceRecord:
    properties = page.get("properties", {})
    if not isinstance(properties, Mapping):
        properties = {}
    title = _text_property(properties.get("Invoice"))
    invoice_id = _text_property(properties.get("Invoice ID")) or title or str(page.get("id", ""))
    status_value = (
        _select_name(properties.get("Processing Status")) or ProcessingStatus.RECEIVED.value
    )
    decision_value = _select_name(properties.get("Human Decision")) or HumanDecision.PENDING.value
    return InvoiceRecord(
        invoice_id=invoice_id,
        page_id=page.get("id") if isinstance(page.get("id"), str) else None,
        invoice_number=_text_property(properties.get("Invoice Number")),
        vendor_page_id=_relation_id(properties.get("Vendor")),
        gstin=_text_property(properties.get("GSTIN")),
        invoice_date=_date_value(properties.get("Invoice Date")),
        due_date=_date_value(properties.get("Due Date")),
        currency=_select_name(properties.get("Currency")),
        subtotal=_decimal_value(properties.get("Subtotal")),
        tax=_decimal_value(properties.get("Tax")),
        total=_decimal_value(properties.get("Total")),
        corrected_total=_decimal_value(properties.get("Corrected Total")),
        po_number=_text_property(properties.get("PO Number")),
        bank_account_reference=_text_property(properties.get("Extracted Bank Account Reference")),
        document_fingerprint=_text_property(properties.get("Document Fingerprint")),
        source=_select_name(properties.get("Source")) or "Upload",
        source_url=_url_value(properties.get("Source URL")),
        extraction_confidence=_decimal_value(properties.get("Extraction Confidence")),
        processing_status=ProcessingStatus(status_value),
        human_decision=HumanDecision(decision_value),
        validation_status=_select_name(properties.get("Validation Status")) or "Pending",
        risk_flags=_multi_select_names(properties.get("Risk Flags")),
        machine_reasoning=_text_property(properties.get("Machine Reasoning")),
        reviewer_notes=_text_property(properties.get("Reviewer Notes")),
        external_action_status=_select_name(properties.get("External Action Status"))
        or "Not Started",
        external_action_id=_text_property(properties.get("External Action ID")),
        decision_processed_at=_datetime_value(properties.get("Decision Processed At")),
        received_at=_datetime_value(properties.get("Received At")) or utc_now(),
        reviewed_at=_datetime_value(properties.get("Reviewed At")),
        approved_at=_datetime_value(properties.get("Approved At")),
        last_updated=_datetime_value(properties.get("Last Updated")) or utc_now(),
    )


def _vendor_from_page(page: Mapping[str, Any]) -> VendorRecord:
    properties = page.get("properties", {})
    if not isinstance(properties, Mapping):
        properties = {}
    vendor_id = _text_property(properties.get("Vendor ID")) or str(page.get("id", ""))
    return VendorRecord(
        vendor_id=vendor_id,
        page_id=page.get("id") if isinstance(page.get("id"), str) else None,
        vendor_name=_text_property(properties.get("Vendor Name")) or vendor_id,
        gstin=_text_property(properties.get("GSTIN")),
        email=_email_value(properties.get("Email")),
        phone=_phone_value(properties.get("Phone")),
        approved_bank_account_reference=_text_property(
            properties.get("Approved Bank Account Reference")
        ),
        payment_terms=_select_name(properties.get("Payment Terms")),
        active_status=_select_name(properties.get("Active Status")) or "Active",
        trusted_vendor=_checkbox_value(properties.get("Trusted Vendor")),
        risk_notes=_text_property(properties.get("Risk Notes")),
    )


def _run_log_from_page(page: Mapping[str, Any], original: RunLogEntry) -> RunLogEntry:
    properties = page.get("properties", {})
    if not isinstance(properties, Mapping):
        properties = {}
    return original.model_copy(
        update={
            "page_id": page.get("id") if isinstance(page.get("id"), str) else None,
            "run_id": _text_property(properties.get("Run ID")) or original.run_id,
            "invoice_page_id": _relation_id(properties.get("Invoice")) or original.invoice_page_id,
        }
    )


def _put_optional(properties: dict[str, Any], name: str, value: Any, original: Any) -> None:
    if original is not None:
        properties[name] = value


def _enum_value(value: Any) -> str | None:
    return value.value if hasattr(value, "value") else value


def _title(value: str | None) -> dict[str, Any]:
    return {"title": [] if value is None else [{"type": "text", "text": {"content": value}}]}


def _rich_text(value: str | None) -> dict[str, Any]:
    return {"rich_text": [] if value is None else [{"type": "text", "text": {"content": value}}]}


def _select(value: str | None) -> dict[str, Any]:
    return {"select": None if value is None else {"name": value}}


def _status(value: str | None) -> dict[str, Any]:
    return {"status": None if value is None else {"name": value}}


def _multi_select(values: list[str] | None) -> dict[str, Any]:
    return {"multi_select": [{"name": value} for value in values or []]}


def _relation(page_id: str | None) -> dict[str, Any]:
    return {"relation": [] if page_id is None else [{"id": page_id}]}


def _date(value: date | datetime | None) -> dict[str, Any]:
    return {"date": None if value is None else {"start": value.isoformat()}}


def _number(value: Decimal | None) -> dict[str, Any]:
    if value is None:
        return {"number": None}
    # Decimal remains the arithmetic type. This conversion exists only at the JSON
    # boundary because Notion's number property is a JSON number.
    numeric: int | float = int(value) if value == value.to_integral_value() else float(value)
    return {"number": numeric}


def _text_property(property_value: Any) -> str | None:
    if not isinstance(property_value, Mapping):
        return None
    kind = property_value.get("type")
    values = property_value.get(kind, [])
    if kind not in {"title", "rich_text"} or not isinstance(values, list):
        return None
    parts = []
    for item in values:
        if isinstance(item, Mapping):
            plain = item.get("plain_text")
            if isinstance(plain, str):
                parts.append(plain)
            else:
                text = item.get("text")
                if isinstance(text, Mapping) and isinstance(text.get("content"), str):
                    parts.append(text["content"])
    return "".join(parts) or None


def _select_name(property_value: Any) -> str | None:
    if not isinstance(property_value, Mapping):
        return None
    selected = property_value.get(property_value.get("type"))
    if isinstance(selected, Mapping) and isinstance(selected.get("name"), str):
        return selected["name"]
    return None


def _decimal_value(property_value: Any) -> Decimal | None:
    if not isinstance(property_value, Mapping):
        return None
    value = property_value.get("number")
    return Decimal(str(value)) if isinstance(value, (int, float, str)) else None


def _date_value(property_value: Any) -> date | None:
    value = _date_string(property_value)
    if value is None:
        return None
    return date.fromisoformat(value[:10])


def _datetime_value(property_value: Any) -> datetime | None:
    value = _date_string(property_value)
    if value is None:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _date_string(property_value: Any) -> str | None:
    if not isinstance(property_value, Mapping):
        return None
    date_value = property_value.get("date")
    return date_value.get("start") if isinstance(date_value, Mapping) else None


def _url_value(property_value: Any) -> str | None:
    return property_value.get("url") if isinstance(property_value, Mapping) else None


def _relation_id(property_value: Any) -> str | None:
    if not isinstance(property_value, Mapping):
        return None
    relation = property_value.get("relation")
    if isinstance(relation, list) and relation and isinstance(relation[0], Mapping):
        value = relation[0].get("id")
        return value if isinstance(value, str) else None
    return None


def _multi_select_names(property_value: Any) -> list[str]:
    if not isinstance(property_value, Mapping):
        return []
    values = property_value.get("multi_select")
    return [
        item["name"]
        for item in values or []
        if isinstance(item, Mapping) and isinstance(item.get("name"), str)
    ]


def _email_value(property_value: Any) -> str | None:
    return property_value.get("email") if isinstance(property_value, Mapping) else None


def _phone_value(property_value: Any) -> str | None:
    return property_value.get("phone_number") if isinstance(property_value, Mapping) else None


def _checkbox_value(property_value: Any) -> bool:
    return bool(property_value.get("checkbox")) if isinstance(property_value, Mapping) else False


def _normalize_vendor_name(value: str) -> str:
    return " ".join(value.casefold().split())
