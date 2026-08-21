import asyncio
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.services.notion_schema_diagnostic import (
    NotionReadOnlyClient,
    NotionSchemaDiagnostic,
    NotionSchemaDiagnosticError,
)

VENDORS_ID = "11111111-1111-4111-8111-111111111111"
INVOICES_ID = "22222222-2222-4222-8222-222222222222"
RUN_LOG_ID = "33333333-3333-4333-8333-333333333333"
VENDORS_DATABASE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
INVOICES_DATABASE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
RUN_LOG_DATABASE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

PROCESSING_OPTIONS = [
    "Received",
    "Extracting",
    "Validating",
    "Needs Review",
    "Approved",
    "Rejected",
    "Actioned",
    "Completed",
    "Failed",
    "Duplicate",
]


def property_schema(property_id: str, property_type: str, **config: Any) -> dict[str, Any]:
    return {"id": property_id, "type": property_type, property_type: config}


def relation_schema(property_id: str, target_id: str, reverse_name: str) -> dict[str, Any]:
    return property_schema(
        property_id,
        "relation",
        data_source_id=target_id,
        type="dual_property",
        dual_property={"synced_property_name": reverse_name},
    )


def data_source(
    source_id: str,
    database_id: str,
    title: str,
    properties: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "object": "data_source",
        "id": source_id,
        "title": [{"plain_text": title}],
        "parent": {"type": "database_id", "database_id": database_id},
        "properties": properties,
    }


def database(database_id: str, source_id: str, title: str) -> dict[str, Any]:
    return {
        "object": "database",
        "id": database_id,
        "title": [{"plain_text": title}],
        "data_sources": [{"id": source_id, "name": title}],
    }


def valid_schemas() -> dict[str, dict[str, Any]]:
    vendors = {
        "Vendor Name": property_schema("v1", "title"),
        "Vendor ID": property_schema("v2", "rich_text"),
        "GSTIN": property_schema("v3", "rich_text"),
        "Email": property_schema("v4", "email"),
        "Phone": property_schema("v5", "phone_number"),
        "Approved Bank Account Reference": property_schema("v6", "rich_text"),
        "Payment Terms": property_schema("v7", "select", options=[]),
        "Active Status": property_schema("v8", "checkbox"),
        "Trusted Vendor": property_schema("v9", "checkbox"),
        "Risk Notes": property_schema("v10", "rich_text"),
        "Invoices": relation_schema("v11", INVOICES_ID, "Vendor"),
    }
    invoices = {
        "Invoice ID": property_schema("i1", "rich_text"),
        "Invoice Number": property_schema("i2", "rich_text"),
        "Vendor": relation_schema("i3", VENDORS_ID, "Invoices"),
        "Invoice Date": property_schema("i4", "date"),
        "Due Date": property_schema("i5", "date"),
        "Currency": property_schema("i6", "rich_text"),
        "Subtotal": property_schema("i7", "number"),
        "Tax": property_schema("i8", "number"),
        "Total": property_schema("i9", "number"),
        "GSTIN": property_schema("i10", "rich_text"),
        "PO Number": property_schema("i11", "rich_text"),
        "Source": property_schema("i12", "rich_text"),
        "Source File": property_schema("i13", "files"),
        "Source URL": property_schema("i14", "url"),
        "Extraction Confidence": property_schema("i15", "number"),
        "Validation Status": property_schema("i16", "select", options=[]),
        "Risk Flags": property_schema("i17", "multi_select", options=[]),
        "Processing Status": property_schema(
            "i18",
            "status",
            options=[
                {"id": str(index), "name": name} for index, name in enumerate(PROCESSING_OPTIONS)
            ],
        ),
        "Human Decision": property_schema(
            "i19",
            "select",
            options=[
                {"id": str(index), "name": name}
                for index, name in enumerate(["Pending", "Approve", "Reject", "Override"])
            ],
        ),
        "Corrected Total": property_schema("i20", "number"),
        "Reviewer Notes": property_schema("i21", "rich_text"),
        "Machine Reasoning": property_schema("i22", "rich_text"),
        "External Action Status": property_schema("i23", "select", options=[]),
        "Document Fingerprint": property_schema("i24", "rich_text"),
        "Extracted Bank Account Reference": property_schema("i25", "rich_text"),
        "Decision Processed At": property_schema("i26", "date"),
        "External Action ID": property_schema("i27", "rich_text"),
        "Received At": property_schema("i28", "date"),
        "Reviewed At": property_schema("i29", "date"),
        "Approved At": property_schema("i30", "date"),
        "Last Updated": property_schema("i31", "date"),
        "Run Log": relation_schema("i32", RUN_LOG_ID, "Invoice"),
    }
    run_log = {
        "Run ID": property_schema("r1", "rich_text"),
        "Timestamp": property_schema("r2", "date"),
        "Invoice": relation_schema("r3", INVOICES_ID, "Run Log"),
        "Trigger": property_schema("r4", "rich_text"),
        "Stage": property_schema("r5", "rich_text"),
        "Result": property_schema("r6", "rich_text"),
        "Summary": property_schema("r7", "rich_text"),
        "Error Message": property_schema("r8", "rich_text"),
        "External Action": property_schema("r9", "rich_text"),
        "Duration": property_schema("r10", "number"),
        "Service Version": property_schema("r11", "rich_text"),
        "Retry Count": property_schema("r12", "number"),
    }
    return {
        VENDORS_ID: data_source(VENDORS_ID, VENDORS_DATABASE_ID, "Vendors", vendors),
        INVOICES_ID: data_source(INVOICES_ID, INVOICES_DATABASE_ID, "Invoices", invoices),
        RUN_LOG_ID: data_source(RUN_LOG_ID, RUN_LOG_DATABASE_ID, "Run Log", run_log),
    }


def diagnostic_with_payloads(
    payloads: dict[str, dict[str, Any]],
    handler_factory: Callable[
        [dict[str, dict[str, Any]]], Callable[[httpx.Request], httpx.Response]
    ],
) -> NotionSchemaDiagnostic:
    transport = httpx.MockTransport(handler_factory(payloads))
    client = NotionReadOnlyClient(
        token="secret-token",
        http_client=httpx.AsyncClient(transport=transport, base_url="https://test"),
    )
    return NotionSchemaDiagnostic(
        client=client,
        data_source_ids={
            "Vendors": VENDORS_ID,
            "Invoices": INVOICES_ID,
            "Run Log": RUN_LOG_ID,
        },
    )


def mock_handler(payloads: dict[str, dict[str, Any]]) -> Callable[[httpx.Request], httpx.Response]:
    schemas = valid_schemas()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.headers["Notion-Version"] == "2026-03-11"
        path_parts = request.url.path.strip("/").split("/")
        if path_parts[0] == "databases":
            database_id = path_parts[1]
            source_id = next(
                source_id
                for source_id, schema in schemas.items()
                if schema["parent"]["database_id"] == database_id
            )
            return httpx.Response(200, json=database(database_id, source_id, source_id))
        source_id = path_parts[1]
        return httpx.Response(200, json=payloads[source_id])

    return handler


def test_valid_schema_passes_and_uses_read_only_gets() -> None:
    payloads = valid_schemas()
    diagnostic = diagnostic_with_payloads(payloads, mock_handler)

    snapshots = asyncio.run(diagnostic.run())

    assert [snapshot.role for snapshot in snapshots] == ["Vendors", "Invoices", "Run Log"]


def test_processing_status_mismatch_fails_clearly() -> None:
    payloads = valid_schemas()
    payloads[INVOICES_ID]["properties"]["Processing Status"]["status"]["options"].pop()
    diagnostic = diagnostic_with_payloads(payloads, mock_handler)

    with pytest.raises(NotionSchemaDiagnosticError, match="Processing Status"):
        asyncio.run(diagnostic.run())
