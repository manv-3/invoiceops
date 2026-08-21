"""Read-only Notion database and data-source schema diagnostics."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

import httpx

JsonObject = dict[str, Any]
ConfidenceScale = Literal["0-1", "0-100"]
NOTION_API_BASE_URL = "https://api.notion.com/v1"
NOTION_API_VERSION = "2026-03-11"

PROCESSING_STATUS_OPTIONS = frozenset(
    {
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
    }
)
HUMAN_DECISION_OPTIONS = frozenset({"Pending", "Approve", "Reject", "Override"})


class NotionApiError(RuntimeError):
    """A safe, non-secret description of a failed read-only Notion request."""

    def __init__(self, status_code: int, path: str, message: str) -> None:
        super().__init__(f"Notion GET {path} failed with HTTP {status_code}: {message}")
        self.status_code = status_code
        self.path = path


class NotionSchemaDiagnosticError(RuntimeError):
    """One or more required data-source schema contracts do not match."""

    def __init__(self, errors: Sequence[str], snapshots: Sequence[DataSourceSnapshot] = ()) -> None:
        self.errors = tuple(errors)
        self.snapshots = tuple(snapshots)
        message = "\n".join(f"- {error}" for error in self.errors)
        super().__init__(f"Notion schema mismatch:\n{message}")


class NotionReadOnlyClient:
    """Minimal Notion client exposing GET operations used by the diagnostic."""

    def __init__(
        self,
        token: str,
        api_version: str = NOTION_API_VERSION,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(
            base_url=NOTION_API_BASE_URL,
            timeout=httpx.Timeout(30.0),
            verify=False,
        )
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Notion-Version": api_version,
            "Accept": "application/json",
        }

    async def __aenter__(self) -> NotionReadOnlyClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client:
            await self._http_client.aclose()

    async def _get(self, path: str) -> JsonObject:
        response = await self._http_client.get(path, headers=self._headers)
        if response.is_error:
            message = "request rejected"
            try:
                body = response.json()
                if isinstance(body, Mapping) and isinstance(body.get("message"), str):
                    message = body["message"]
            except ValueError:
                pass
            raise NotionApiError(response.status_code, path, message[:300])

        body = response.json()
        if not isinstance(body, dict):
            raise NotionApiError(response.status_code, path, "response was not a JSON object")
        return body

    async def retrieve_data_source(self, data_source_id: str) -> JsonObject:
        """Retrieve a data source schema by its data-source ID."""

        return await self._get(f"/data_sources/{_uuid_path_value(data_source_id)}")

    async def retrieve_database(self, database_id: str) -> JsonObject:
        """Retrieve the containing database by its database ID."""

        return await self._get(f"/databases/{_uuid_path_value(database_id)}")


@dataclass(frozen=True)
class NotionProperty:
    """Normalized property schema information used by contract checks."""

    name: str
    property_id: str
    property_type: str
    config: Mapping[str, Any]

    @classmethod
    def from_schema(cls, name: str, schema: Mapping[str, Any]) -> NotionProperty:
        property_type = schema.get("type")
        property_id = schema.get("id")
        if not isinstance(property_type, str) or not isinstance(property_id, str):
            raise ValueError(f"property {name!r} is missing a string id or type")
        config = schema.get(property_type, {})
        if not isinstance(config, Mapping):
            raise ValueError(f"property {name!r} has an invalid {property_type} configuration")
        return cls(name, property_id, property_type, config)

    def option_names(self) -> frozenset[str]:
        options = self.config.get("options", [])
        if not isinstance(options, list):
            return frozenset()
        return frozenset(
            option["name"]
            for option in options
            if isinstance(option, Mapping) and isinstance(option.get("name"), str)
        )

    def relation_target_data_source_id(self) -> str | None:
        if self.property_type != "relation":
            return None
        target = self.config.get("data_source_id")
        return target if isinstance(target, str) else None

    def relation_reverse_name(self) -> str | None:
        if self.property_type != "relation":
            return None
        dual_property = self.config.get("dual_property")
        if not isinstance(dual_property, Mapping):
            return None
        name = dual_property.get("synced_property_name")
        return name if isinstance(name, str) else None


@dataclass(frozen=True)
class DataSourceSnapshot:
    """Retrieved schema plus its containing database identity."""

    role: str
    data_source_id: str
    database_id: str
    title: str
    properties: Mapping[str, NotionProperty]


class NotionSchemaDiagnostic:
    """Retrieve and validate Vendors, Invoices, and Run Log data sources."""

    def __init__(
        self,
        client: NotionReadOnlyClient,
        data_source_ids: Mapping[str, str],
        confidence_scale: ConfidenceScale = "0-1",
    ) -> None:
        self.client = client
        self.data_source_ids = dict(data_source_ids)
        self.confidence_scale = confidence_scale

    async def collect(self) -> list[DataSourceSnapshot]:
        snapshots: list[DataSourceSnapshot] = []
        for role in ("Vendors", "Invoices", "Run Log"):
            data_source_id = self.data_source_ids.get(role)
            if not data_source_id:
                raise NotionSchemaDiagnosticError([f"missing {role} data-source ID"])

            metadata = await self.client.retrieve_data_source(data_source_id)
            database_id = _parent_database_id(metadata, role)
            database = await self.client.retrieve_database(database_id)
            _ensure_database_contains_source(database, data_source_id, role)

            schema = await self.client.retrieve_data_source(data_source_id)
            properties = schema.get("properties")
            if not isinstance(properties, Mapping):
                raise NotionSchemaDiagnosticError(
                    [f"{role} data source returned no properties schema"]
                )
            normalized = {
                name: NotionProperty.from_schema(name, definition)
                for name, definition in properties.items()
                if isinstance(name, str) and isinstance(definition, Mapping)
            }
            if len(normalized) != len(properties):
                raise NotionSchemaDiagnosticError(
                    [f"{role} data source contains an invalid property definition"]
                )
            snapshots.append(
                DataSourceSnapshot(
                    role=role,
                    data_source_id=data_source_id,
                    database_id=database_id,
                    title=_title_from_object(schema, role),
                    properties=normalized,
                )
            )
        return snapshots

    async def run(self) -> list[DataSourceSnapshot]:
        snapshots = await self.collect()
        errors = validate_schema_contracts(snapshots, self.confidence_scale)
        if errors:
            raise NotionSchemaDiagnosticError(errors, snapshots)
        return snapshots


def validate_schema_contracts(
    snapshots: Sequence[DataSourceSnapshot],
    confidence_scale: ConfidenceScale = "0-1",
) -> list[str]:
    """Return all contract mismatches without changing Notion."""

    by_role = {snapshot.role: snapshot for snapshot in snapshots}
    errors: list[str] = []
    for role in ("Vendors", "Invoices", "Run Log"):
        if role not in by_role:
            errors.append(f"missing {role} data-source schema")

    if errors:
        return errors

    vendors = by_role["Vendors"]
    invoices = by_role["Invoices"]
    run_log = by_role["Run Log"]

    errors.extend(
        _check_types(
            vendors,
            {
                "Vendor Name": {"title", "rich_text"},
                "Vendor ID": {"title", "rich_text"},
                "GSTIN": {"rich_text"},
                "Email": {"email"},
                "Phone": {"phone_number"},
                "Approved Bank Account Reference": {"rich_text"},
                "Payment Terms": {"rich_text", "select"},
                "Active Status": {"checkbox", "select", "status"},
                "Trusted Vendor": {"checkbox"},
                "Risk Notes": {"rich_text"},
                "Invoices": {"relation"},
            },
        )
    )
    errors.extend(
        _check_types(
            invoices,
            {
                "Invoice ID": {"title", "rich_text"},
                "Invoice Number": {"rich_text"},
                "Vendor": {"relation"},
                "Invoice Date": {"date"},
                "Due Date": {"date"},
                "Currency": {"rich_text", "select"},
                "Subtotal": {"number"},
                "Tax": {"number"},
                "Total": {"number"},
                "Extraction Confidence": {"number"},
                "GSTIN": {"rich_text"},
                "PO Number": {"rich_text"},
                "Source": {"rich_text", "select"},
                "Source File": {"files", "rich_text"},
                "Source URL": {"url"},
                "Validation Status": {"select", "status"},
                "Risk Flags": {"multi_select"},
                "Processing Status": {"status"},
                "Human Decision": {"select"},
                "Corrected Total": {"number"},
                "Reviewer Notes": {"rich_text"},
                "Machine Reasoning": {"rich_text"},
                "External Action Status": {"select", "status"},
                "Document Fingerprint": {"rich_text"},
                "Extracted Bank Account Reference": {"rich_text"},
                "Decision Processed At": {"date"},
                "External Action ID": {"rich_text"},
                "Received At": {"date"},
                "Reviewed At": {"date"},
                "Approved At": {"date"},
                "Last Updated": {"date"},
                "Run Log": {"relation"},
            },
        )
    )
    errors.extend(
        _check_types(
            run_log,
            {
                "Run ID": {"title", "rich_text"},
                "Timestamp": {"date"},
                "Invoice": {"relation"},
                "Trigger": {"rich_text", "select"},
                "Stage": {"rich_text", "select"},
                "Result": {"rich_text", "select"},
                "Summary": {"rich_text"},
                "Error Message": {"rich_text"},
                "External Action": {"rich_text", "select"},
                "Service Version": {"rich_text"},
                "Duration": {"number"},
                "Retry Count": {"number"},
            },
        )
    )

    errors.extend(_check_options(invoices, "Processing Status", PROCESSING_STATUS_OPTIONS))
    errors.extend(_check_options(invoices, "Human Decision", HUMAN_DECISION_OPTIONS))
    errors.extend(
        _check_relations(
            vendors,
            invoices,
            run_log,
            {
                ("Vendors", "Invoices"): (invoices.data_source_id, "Vendor"),
                ("Invoices", "Vendor"): (vendors.data_source_id, "Invoices"),
                ("Invoices", "Run Log"): (run_log.data_source_id, "Invoice"),
                ("Run Log", "Invoice"): (invoices.data_source_id, "Run Log"),
            },
        )
    )

    for property_name in ("Subtotal", "Tax", "Total", "Corrected Total"):
        errors.extend(_check_required_property(invoices, property_name))
    if confidence_scale not in {"0-1", "0-100"}:
        errors.append(f"confidence scale must be 0-1 or 0-100, got {confidence_scale!r}")
    return errors


def render_schema_report(
    snapshots: Sequence[DataSourceSnapshot], confidence_scale: ConfidenceScale = "0-1"
) -> str:
    """Render names, IDs, types, options, and relation targets without secrets."""

    lines = [f"Confidence scale contract: {confidence_scale}"]
    for snapshot in snapshots:
        lines.append(
            f"{snapshot.role}: title={snapshot.title!r} "
            f"data_source_id={snapshot.data_source_id} database_id={snapshot.database_id}"
        )
        for property_name in sorted(snapshot.properties):
            prop = snapshot.properties[property_name]
            details = [f"id={prop.property_id}", f"type={prop.property_type}"]
            options = sorted(prop.option_names())
            if options:
                details.append(f"options={options}")
            target = prop.relation_target_data_source_id()
            if target:
                details.append(f"relation_data_source_id={target}")
                reverse = prop.relation_reverse_name()
                if reverse:
                    details.append(f"reverse_property={reverse!r}")
            lines.append(f"  - {property_name}: " + ", ".join(details))
    return "\n".join(lines)


def _check_types(snapshot: DataSourceSnapshot, expected: Mapping[str, set[str]]) -> list[str]:
    errors: list[str] = []
    for property_name, allowed_types in expected.items():
        prop = snapshot.properties.get(property_name)
        if prop is None:
            errors.append(f"{snapshot.role}: missing property {property_name!r}")
        elif prop.property_type not in allowed_types:
            allowed = ", ".join(sorted(allowed_types))
            errors.append(
                f"{snapshot.role}: {property_name!r} must be type {allowed}, "
                f"found {prop.property_type!r}"
            )
    return errors


def _check_required_property(snapshot: DataSourceSnapshot, property_name: str) -> list[str]:
    if property_name not in snapshot.properties:
        return [f"{snapshot.role}: missing required money property {property_name!r}"]
    return []


def _check_options(
    snapshot: DataSourceSnapshot, property_name: str, expected: frozenset[str]
) -> list[str]:
    prop = snapshot.properties.get(property_name)
    if prop is None:
        return [f"{snapshot.role}: missing option property {property_name!r}"]
    actual = prop.option_names()
    if actual != expected:
        return [
            f"{snapshot.role}: {property_name!r} options mismatch; "
            f"expected {sorted(expected)}, found {sorted(actual)}"
        ]
    return []


def _check_relations(
    vendors: DataSourceSnapshot,
    invoices: DataSourceSnapshot,
    run_log: DataSourceSnapshot,
    expected: Mapping[tuple[str, str], tuple[str, str]],
) -> list[str]:
    snapshots = {snapshot.role: snapshot for snapshot in (vendors, invoices, run_log)}
    errors: list[str] = []
    for (role, property_name), (target_id, reverse_name) in expected.items():
        prop = snapshots[role].properties.get(property_name)
        if prop is None or prop.property_type != "relation":
            continue
        if prop.relation_target_data_source_id() != target_id:
            errors.append(
                f"{role}: relation {property_name!r} must target data source {target_id}, "
                f"found {prop.relation_target_data_source_id()!r}"
            )
        if prop.relation_reverse_name() != reverse_name:
            errors.append(
                f"{role}: relation {property_name!r} must reverse to {reverse_name!r}, "
                f"found {prop.relation_reverse_name()!r}"
            )

    for property_name, prop in vendors.properties.items():
        if (
            prop.property_type == "relation"
            and prop.relation_target_data_source_id() == run_log.data_source_id
        ):
            errors.append(
                f"Vendors: relation {property_name!r} must not target Run Log; "
                "Vendor reverse relation must come from Vendor–Invoice"
            )
    return errors


def _parent_database_id(source: Mapping[str, Any], role: str) -> str:
    parent = source.get("parent")
    if not isinstance(parent, Mapping):
        raise NotionSchemaDiagnosticError([f"{role}: data source response has no parent database"])
    database_id = parent.get("database_id")
    if not isinstance(database_id, str):
        raise NotionSchemaDiagnosticError(
            [f"{role}: data source parent is not a database_id parent"]
        )
    return _uuid_path_value(database_id)


def _ensure_database_contains_source(
    database: Mapping[str, Any], data_source_id: str, role: str
) -> None:
    if database.get("object") != "database":
        raise NotionSchemaDiagnosticError([f"{role}: containing object is not a database"])
    source_ids = {
        source.get("id")
        for source in database.get("data_sources", [])
        if isinstance(source, Mapping)
    }
    if data_source_id not in source_ids:
        raise NotionSchemaDiagnosticError(
            [f"{role}: database does not list configured data source {data_source_id}"]
        )


def _title_from_object(source: Mapping[str, Any], fallback: str) -> str:
    title = source.get("title")
    if isinstance(title, list):
        for item in title:
            if isinstance(item, Mapping) and isinstance(item.get("plain_text"), str):
                return item["plain_text"]
    return fallback


def _uuid_path_value(value: str) -> str:
    try:
        return str(UUID(value))
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"expected a UUID, got {value!r}") from exc


async def run_configured_diagnostic() -> int:
    """Run the CLI diagnostic using environment-backed application settings."""

    from app.config import get_settings

    settings = get_settings()
    if settings.notion_token is None:
        print("NOTION_SCHEMA_DIAGNOSTIC_FAILED: NOTION_TOKEN is missing", file=sys.stderr)
        return 1

    data_source_ids = {
        "Vendors": settings.notion_vendors_data_source_id,
        "Invoices": settings.notion_invoices_data_source_id,
        "Run Log": settings.notion_run_log_data_source_id,
    }
    missing = [role for role, data_source_id in data_source_ids.items() if not data_source_id]
    if missing:
        print(
            "NOTION_SCHEMA_DIAGNOSTIC_FAILED: missing data-source IDs for " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1

    async with NotionReadOnlyClient(
        settings.notion_token.get_secret_value(), settings.notion_api_version
    ) as client:
        diagnostic = NotionSchemaDiagnostic(
            client=client,
            data_source_ids={
                role: data_source_id
                for role, data_source_id in data_source_ids.items()
                if data_source_id
            },
            confidence_scale=settings.confidence_scale,
        )
        try:
            snapshots = await diagnostic.run()
        except NotionSchemaDiagnosticError as exc:
            if exc.snapshots:
                print(render_schema_report(exc.snapshots, settings.confidence_scale))
            print(f"NOTION_SCHEMA_DIAGNOSTIC_FAILED: {exc}", file=sys.stderr)
            return 1
        except (NotionApiError, ValueError, httpx.HTTPError) as exc:
            print(f"NOTION_SCHEMA_DIAGNOSTIC_FAILED: {exc}", file=sys.stderr)
            return 1

    print(render_schema_report(snapshots, settings.confidence_scale))
    print("NOTION_SCHEMA_DIAGNOSTIC_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run_configured_diagnostic()))
