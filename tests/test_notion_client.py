import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from app.models.invoice import InvoiceRecord, InvoiceUpdate, RunLogEntry, VendorRecord
from app.models.workflow import HumanDecision, ProcessingStatus
from app.services.notion import NotionClient

INVOICES_ID = "11111111-1111-4111-8111-111111111111"
VENDORS_ID = "22222222-2222-4222-8222-222222222222"
RUN_LOG_ID = "33333333-3333-4333-8333-333333333333"
PAGE_ID = "44444444-4444-4444-8444-444444444444"


def invoice() -> InvoiceRecord:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    return InvoiceRecord(
        invoice_id="INVOPS-TEST-001",
        invoice_number="TEST-001",
        vendor_name="Test Vendor",
        total=Decimal("100.25"),
        document_fingerprint="a" * 64,
        processing_status=ProcessingStatus.RECEIVED,
        human_decision=HumanDecision.PENDING,
        received_at=timestamp,
        last_updated=timestamp,
    )


def page_payload() -> dict[str, Any]:
    return {
        "object": "page",
        "id": PAGE_ID,
        "properties": {
            "Invoice": {
                "id": "title",
                "type": "title",
                "title": [{"plain_text": "INVOPS-TEST-001"}],
            },
            "Invoice ID": {
                "id": "id",
                "type": "rich_text",
                "rich_text": [{"plain_text": "INVOPS-TEST-001"}],
            },
            "Invoice Number": {
                "id": "number",
                "type": "rich_text",
                "rich_text": [{"plain_text": "TEST-001"}],
            },
            "Vendor": {"id": "vendor", "type": "relation", "relation": []},
            "Total": {"id": "total", "type": "number", "number": 100.25},
            "Processing Status": {"id": "status", "type": "status", "status": {"name": "Received"}},
            "Human Decision": {"id": "decision", "type": "select", "select": {"name": "Pending"}},
            "Document Fingerprint": {
                "id": "fingerprint",
                "type": "rich_text",
                "rich_text": [{"plain_text": "a" * 64}],
            },
            "Received At": {
                "id": "received",
                "type": "date",
                "date": {"start": "2026-01-01T00:00:00Z"},
            },
            "Last Updated": {
                "id": "updated",
                "type": "date",
                "date": {"start": "2026-01-01T00:00:00Z"},
            },
        },
    }


def run_log_payload() -> dict[str, Any]:
    return {
        "object": "page",
        "id": "55555555-5555-4555-8555-555555555555",
        "properties": {
            "Run": {"id": "title", "type": "title", "title": [{"plain_text": "RUN-1"}]},
            "Run ID": {"id": "run", "type": "rich_text", "rich_text": [{"plain_text": "RUN-1"}]},
            "Invoice": {"id": "invoice", "type": "relation", "relation": [{"id": PAGE_ID}]},
            "Timestamp": {
                "id": "timestamp",
                "type": "date",
                "date": {"start": "2026-01-01T00:00:00Z"},
            },
            "Trigger": {"id": "trigger", "type": "select", "select": {"name": "Upload"}},
            "Stage": {"id": "stage", "type": "select", "select": {"name": "Intake"}},
            "Result": {"id": "result", "type": "select", "select": {"name": "Success"}},
            "Summary": {
                "id": "summary",
                "type": "rich_text",
                "rich_text": [{"plain_text": "Stored"}],
            },
            "Duration": {"id": "duration", "type": "number", "number": 1},
            "Retry Count": {"id": "retry", "type": "number", "number": 0},
            "Service Version": {
                "id": "version",
                "type": "rich_text",
                "rich_text": [{"plain_text": "0.1.0"}],
            },
        },
    }


def vendor_payload() -> dict[str, Any]:
    return {
        "object": "page",
        "id": "66666666-6666-4666-8666-666666666666",
        "properties": {
            "Vendor Name": {
                "id": "title",
                "type": "title",
                "title": [{"plain_text": "Test Vendor"}],
            },
            "Vendor ID": {
                "id": "vendor-id",
                "type": "rich_text",
                "rich_text": [{"plain_text": "V-1"}],
            },
            "Trusted Vendor": {"id": "trusted", "type": "checkbox", "checkbox": True},
            "Active Status": {
                "id": "active",
                "type": "select",
                "select": {"name": "Active"},
            },
        },
    }


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> NotionClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="https://test")
    return NotionClient(
        token="secret-token",
        invoices_data_source_id=INVOICES_ID,
        vendors_data_source_id=VENDORS_ID,
        run_log_data_source_id=RUN_LOG_ID,
        http_client=http_client,
    )


def test_create_invoice_uses_data_source_parent_and_maps_decimal() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/pages"
        body = httpx.Request("POST", "https://test", content=request.content).read()
        assert f'"data_source_id":"{INVOICES_ID}"'.encode() in body
        assert b'"Document Fingerprint"' in body
        assert b'"number":100.25' in body
        return httpx.Response(200, json=page_payload())

    async def run() -> InvoiceRecord:
        client = client_for(handler)
        try:
            return await client.create_invoice(invoice())
        finally:
            await client.close()

    created = asyncio.run(run())
    assert created.page_id == PAGE_ID
    assert created.total == Decimal("100.25")


def test_get_invoice_queries_by_invoice_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == f"/data_sources/{INVOICES_ID}/query"
        assert b'"Invoice ID"' in request.content
        return httpx.Response(200, json={"results": [page_payload()], "has_more": False})

    async def run() -> InvoiceRecord | None:
        client = client_for(handler)
        try:
            return await client.get_invoice("INVOPS-TEST-001")
        finally:
            await client.close()

    found = asyncio.run(run())
    assert found is not None
    assert found.invoice_id == "INVOPS-TEST-001"


def test_create_run_log_uses_run_log_data_source() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/pages"
        assert f'"data_source_id":"{RUN_LOG_ID}"'.encode() in request.content
        return httpx.Response(200, json=run_log_payload())

    entry = RunLogEntry(
        run_id="RUN-1",
        invoice_id="INVOPS-TEST-001",
        trigger="Upload",
        stage="Intake",
        result="Success",
        summary="Stored",
        duration_ms=1,
    )

    async def run() -> RunLogEntry:
        client = client_for(handler)
        try:
            return await client.create_run_log(entry, PAGE_ID)
        finally:
            await client.close()

    created = asyncio.run(run())
    assert created.run_id == "RUN-1"


def test_update_invoice_maps_writable_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith(f"/data_sources/{INVOICES_ID}/query"):
            return httpx.Response(200, json={"results": [page_payload()]})
        assert request.method == "PATCH"
        assert request.url.path == f"/pages/{PAGE_ID}"
        assert b'"External Action ID"' in request.content
        assert b'"External Action Status"' in request.content
        assert b'"Vendor":{"relation":[]}' in request.content
        assert b'"Total":{"number":null}' in request.content
        assert b'"Invoice Date":{"date":null}' in request.content
        return httpx.Response(200, json=page_payload())

    async def run() -> InvoiceRecord:
        client = client_for(handler)
        try:
            return await client.update_invoice(
                "INVOPS-TEST-001",
                InvoiceUpdate(
                    external_action_id="ACTION-1",
                    external_action_status="Success",
                    vendor_page_id=None,
                    total=None,
                    invoice_date=None,
                ),
            )
        finally:
            await client.close()

    updated = asyncio.run(run())
    assert updated.page_id == PAGE_ID


def test_create_vendor_uses_vendor_data_source() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/pages"
        assert f'"data_source_id":"{VENDORS_ID}"'.encode() in request.content
        assert b'"Vendor Name"' in request.content
        return httpx.Response(200, json=vendor_payload())

    vendor = VendorRecord(vendor_id="V-1", vendor_name="Test Vendor", trusted_vendor=True)

    async def run() -> VendorRecord:
        client = client_for(handler)
        try:
            return await client.create_vendor(vendor)
        finally:
            await client.close()

    created = asyncio.run(run())
    assert created.vendor_id == "V-1"
    assert created.trusted_vendor is True


def test_find_vendor_checks_gstin_then_exact_and_normalized_name() -> None:
    calls: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = httpx.Request("POST", "https://test", content=request.content).read()
        calls.append(httpx.Response(200, content=body).json())
        if len(calls) < 3:
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"results": [vendor_payload()]})

    async def run() -> VendorRecord | None:
        client = client_for(handler)
        try:
            return await client.find_vendor("  Test   Vendor ", gstin="GST-1")
        finally:
            await client.close()

    found = asyncio.run(run())
    assert found is not None
    assert found.vendor_name == "Test Vendor"
    assert "GSTIN" in calls[0]["filter"]["property"]
    assert calls[1]["filter"]["property"] == "Vendor Name"
    assert "filter" not in calls[2]


def test_query_follows_notion_pagination() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(
                200, json={"results": [], "has_more": True, "next_cursor": "cursor-2"}
            )
        assert b'"start_cursor":"cursor-2"' in request.content
        return httpx.Response(200, json={"results": [vendor_payload()], "has_more": False})

    async def run() -> VendorRecord | None:
        client = client_for(handler)
        try:
            return await client.find_vendor("Missing Vendor")
        finally:
            await client.close()

    found = asyncio.run(run())
    assert found is not None
    assert calls == 2


def test_attach_invoice_file_uploads_and_patches_source_file(tmp_path) -> None:
    source = tmp_path / "INVOPS-TEST-001.pdf"
    source.write_bytes(b"%PDF-test")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith(f"/data_sources/{INVOICES_ID}/query"):
            return httpx.Response(200, json={"results": [page_payload()]})
        if request.url.path == "/file_uploads":
            assert b'"mode":"single_part"' in request.content
            return httpx.Response(
                200,
                json={"id": "upload-1", "upload_url": "https://test/file_uploads/upload-1/send"},
            )
        if request.url.path == "/file_uploads/upload-1/send":
            assert request.headers["content-type"].startswith("multipart/form-data")
            return httpx.Response(200, json={"id": "upload-1", "status": "uploaded"})
        assert request.method == "PATCH"
        assert request.url.path == f"/pages/{PAGE_ID}"
        assert b'"Source File"' in request.content
        assert b'"file_upload"' in request.content
        return httpx.Response(200, json=page_payload())

    async def run() -> InvoiceRecord:
        client = client_for(handler)
        try:
            return await client.attach_invoice_file("INVOPS-TEST-001", source)
        finally:
            await client.close()

    attached = asyncio.run(run())
    assert attached.page_id == PAGE_ID


def test_transient_notion_error_is_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"message": "temporary"})
        return httpx.Response(200, json={"results": [page_payload()]})

    async def run() -> InvoiceRecord | None:
        client = client_for(handler)
        try:
            return await client.get_invoice("INVOPS-TEST-001")
        finally:
            await client.close()

    found = asyncio.run(run())
    assert found is not None
    assert calls == 2
