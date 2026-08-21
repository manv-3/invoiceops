"""Tests for the invoice upload endpoint."""

import asyncio

import httpx

from app.main import app
from app.services import store
from app.services.run_log import clear as clear_log


def setup_function() -> None:
    store.clear()
    clear_log()


def test_upload_pdf_returns_201() -> None:
    async def _upload() -> httpx.Response:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/invoices/upload",
                    files={
                        "file": (
                            "invoice.pdf",
                            b"%PDF-1.4 test content",
                            "application/pdf",
                        )
                    },
                )

    response = asyncio.run(_upload())
    assert response.status_code == 201
    data = response.json()
    assert data["status"] in ("approved", "validated", "needs_review")
    assert data["fingerprint"]
    assert data["original_filename"] == "invoice.pdf"


def test_upload_rejects_unsupported_type() -> None:
    async def _upload() -> httpx.Response:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post(
                    "/invoices/upload",
                    files={"file": ("doc.txt", b"hello", "text/plain")},
                )

    response = asyncio.run(_upload())
    assert response.status_code == 400


def test_get_invoice_returns_record() -> None:
    async def _run() -> tuple[httpx.Response, httpx.Response]:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                upload_resp = await client.post(
                    "/invoices/upload",
                    files={
                        "file": (
                            "test.pdf",
                            b"%PDF-1.4 data",
                            "application/pdf",
                        )
                    },
                )
                invoice_id = upload_resp.json()["id"]
                get_resp = await client.get(f"/invoices/{invoice_id}")
                return upload_resp, get_resp

    upload_resp, get_resp = asyncio.run(_run())
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == upload_resp.json()["id"]


def test_get_missing_invoice_returns_404() -> None:
    async def _run() -> httpx.Response:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get("/invoices/nonexistent")

    response = asyncio.run(_run())
    assert response.status_code == 404
