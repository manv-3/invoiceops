import asyncio
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.main import app
from app.models.invoice import InvoiceRecord, RunLogEntry
from app.models.workflow import HumanDecision, ProcessingStatus
from app.utils.hashing import sha256_bytes


class FakeNotionClient:
    def __init__(self) -> None:
        self.invoices: dict[str, InvoiceRecord] = {}
        self.run_logs: list[RunLogEntry] = []

    async def create_invoice(self, invoice: InvoiceRecord) -> InvoiceRecord:
        stored = invoice.model_copy(update={"page_id": f"page-{invoice.invoice_id}"})
        self.invoices[stored.invoice_id] = stored
        return stored

    async def create_run_log(
        self, entry: RunLogEntry, invoice_page_id: str | None = None
    ) -> RunLogEntry:
        self.run_logs.append(entry.model_copy(update={"invoice_page_id": invoice_page_id}))
        return self.run_logs[-1]

    async def get_invoice(self, invoice_id: str) -> InvoiceRecord | None:
        return self.invoices.get(invoice_id)


async def request(
    fake_notion: FakeNotionClient,
    method: str,
    path: str,
    **kwargs: Any,
) -> httpx.Response:
    async with app.router.lifespan_context(app):
        app.state.notion_client = fake_notion
        app.state.settings = Settings(
            _env_file=None, max_upload_mb=1, storage_dir=Path("storage/test")
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.request(method, path, **kwargs)


def test_valid_invoice_upload_stores_bytes_and_writes_invoice_and_run_log(tmp_path: Path) -> None:
    fake = FakeNotionClient()
    document = b"%PDF-1.7\nrealistic invoice fixture bytes"

    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            app.state.notion_client = fake
            app.state.settings = Settings(_env_file=None, max_upload_mb=1, storage_dir=tmp_path)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/webhooks/invoices",
                    files={"file": ("../../secret.pdf", document, "application/pdf")},
                )

    response = asyncio.run(run())
    assert response.status_code == 201
    body = response.json()
    invoice_id = body["invoice_id"]
    assert invoice_id in fake.invoices
    assert len(fake.run_logs) == 1
    assert fake.run_logs[0].stage == "Intake"
    assert fake.invoices[invoice_id].processing_status is ProcessingStatus.RECEIVED
    assert fake.invoices[invoice_id].human_decision is HumanDecision.PENDING
    assert fake.invoices[invoice_id].document_fingerprint == sha256_bytes(document)
    stored_files = list(tmp_path.joinpath("incoming").iterdir())
    assert len(stored_files) == 1
    assert stored_files[0].name != "../../secret.pdf"
    assert stored_files[0].read_bytes() == document


def test_unsupported_mime_type_is_rejected() -> None:
    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            app.state.notion_client = FakeNotionClient()
            app.state.settings = Settings(
                _env_file=None, max_upload_mb=1, storage_dir=Path("storage/test")
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/webhooks/invoices",
                    files={"file": ("invoice.txt", b"not an invoice", "text/plain")},
                )

    response = asyncio.run(run())
    assert response.status_code == 415


def test_mime_type_and_file_signature_must_match() -> None:
    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            app.state.notion_client = FakeNotionClient()
            app.state.settings = Settings(
                _env_file=None, max_upload_mb=1, storage_dir=Path("storage/test")
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/webhooks/invoices",
                    files={"file": ("invoice.pdf", b"not-a-pdf", "application/pdf")},
                )

    response = asyncio.run(run())
    assert response.status_code == 415


def test_oversized_upload_is_rejected(tmp_path: Path) -> None:
    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            app.state.notion_client = FakeNotionClient()
            app.state.settings = Settings(_env_file=None, max_upload_mb=1, storage_dir=tmp_path)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/webhooks/invoices",
                    files={
                        "file": ("large.pdf", b"%PDF" + b"x" * (1024 * 1024), "application/pdf")
                    },
                )

    response = asyncio.run(run())
    assert response.status_code == 413


def test_invoice_status_endpoint_reads_current_state() -> None:
    fake = FakeNotionClient()
    invoice = InvoiceRecord(invoice_id="INVOPS-status", page_id="page-status")
    fake.invoices[invoice.invoice_id] = invoice

    async def run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            app.state.notion_client = fake
            app.state.settings = Settings(_env_file=None, storage_dir=Path("storage/test"))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get(f"/api/invoices/{invoice.invoice_id}/status")

    response = asyncio.run(run())
    assert response.status_code == 200
    assert response.json()["processing_status"] == "Received"
