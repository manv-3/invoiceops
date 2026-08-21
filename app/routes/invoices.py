"""Invoice intake and development status endpoints."""

from __future__ import annotations

import hmac
from io import BytesIO
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.config import Settings, get_settings
from app.models.invoice import InvoiceIntakeResponse, InvoiceRecord, InvoiceStatusResponse
from app.services.extractor import ExtractionError
from app.services.invoice_processing import InvoiceProcessingService
from app.services.invoice_service import InvoiceIntakeService, UploadError, stored_document_path
from app.services.notion import NotionApiError, NotionClient
from app.services.telegram import TelegramApiError, TelegramClient, extract_document_reference

router = APIRouter(tags=["invoices"])


def runtime_settings(request: Request) -> Settings:
    return getattr(request.app.state, "settings", get_settings())


def notion_client(request: Request, settings: Settings) -> NotionClient:
    existing = getattr(request.app.state, "notion_client", None)
    if existing is not None:
        return existing
    if (
        settings.notion_token is None
        or not settings.notion_invoices_data_source_id
        or not settings.notion_vendors_data_source_id
        or not settings.notion_run_log_data_source_id
    ):
        raise HTTPException(status_code=503, detail="Notion integration is not configured")
    client = NotionClient(
        token=settings.notion_token.get_secret_value(),
        invoices_data_source_id=settings.notion_invoices_data_source_id,
        vendors_data_source_id=settings.notion_vendors_data_source_id,
        run_log_data_source_id=settings.notion_run_log_data_source_id,
        api_version=settings.notion_api_version,
    )
    request.app.state.notion_client = client
    request.app.state.invoiceops_owned_notion_client = True
    return client


@router.post(
    "/webhooks/invoices",
    response_model=InvoiceIntakeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest an invoice document",
)
async def upload_invoice(
    request: Request,
    file: Annotated[UploadFile, File(description="PDF, PNG, JPG, or JPEG invoice")],
) -> InvoiceIntakeResponse:
    settings = runtime_settings(request)
    client = notion_client(request, settings)
    service = InvoiceIntakeService(client, settings)
    try:
        invoice = await service.ingest(file)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except NotionApiError as exc:
        print(f"Notion API Error: {exc}")
        raise HTTPException(status_code=502, detail="Notion write failed") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    invoice = await process_ingested_invoice(request, settings, client, invoice)
    return InvoiceIntakeResponse(
        invoice_id=invoice.invoice_id,
        page_id=invoice.page_id,
        processing_status=invoice.processing_status,
        document_fingerprint=invoice.document_fingerprint or "",
        source="Upload",
    )


async def process_ingested_invoice(
    request: Request, settings: Settings, client: NotionClient, invoice: InvoiceRecord
) -> InvoiceRecord:
    if not (
        settings.auto_process_clean_invoices and settings.gemini_api_key and settings.gemini_model
    ):
        return invoice
    processor = getattr(request.app.state, "invoice_processor", None)
    if processor is None:
        processor = InvoiceProcessingService(client, settings)
        request.app.state.invoice_processor = processor
    try:
        return await processor.process(
            invoice.invoice_id, stored_document_path(settings, invoice.invoice_id)
        )
    except ExtractionError:
        try:
            current = await client.get_invoice(invoice.invoice_id)
        except NotionApiError as exc:
            raise HTTPException(status_code=502, detail="Notion processing read failed") from exc
        return current or invoice
    except NotionApiError as exc:
        raise HTTPException(status_code=502, detail="Notion processing write failed") from exc


@router.post("/webhooks/telegram", summary="Receive Telegram invoice messages")
async def telegram_webhook(request: Request) -> dict[str, Any]:
    settings = runtime_settings(request)
    if settings.telegram_bot_token is None:
        raise HTTPException(status_code=503, detail="Telegram integration is not configured")
    if settings.telegram_webhook_secret is None and settings.app_env == "production":
        raise HTTPException(status_code=503, detail="Telegram webhook secret is required")
    if settings.telegram_webhook_secret is not None:
        supplied = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(supplied, settings.telegram_webhook_secret.get_secret_value()):
            raise HTTPException(status_code=403, detail="invalid Telegram webhook secret")
    try:
        update = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Telegram update is not valid JSON") from exc
    if not isinstance(update, dict):
        raise HTTPException(status_code=400, detail="Telegram update must be a JSON object")
    reference = extract_document_reference(update)
    if reference is None:
        return {"status": "ignored", "reason": "message did not contain an invoice document"}

    chat_id, file_id, filename, mime_type = reference
    telegram = TelegramClient(
        settings.telegram_bot_token.get_secret_value(), settings.telegram_api_base_url
    )
    try:
        file_path = await telegram.get_file_path(file_id)
        content = await telegram.download_file(file_path)
        if len(content) > settings.max_upload_mb * 1024 * 1024:
            await telegram.send_message(
                chat_id, "Invoice rejected: document exceeds the size limit."
            )
            return {"status": "rejected", "reason": "document too large"}
        client = notion_client(request, settings)
        upload = UploadFile(
            file=BytesIO(content), filename=filename, headers={"content-type": mime_type}
        )
        invoice = await InvoiceIntakeService(client, settings).ingest(upload)
        invoice = await process_ingested_invoice(request, settings, client, invoice)
        await telegram.send_message(
            chat_id,
            f"Invoice {invoice.invoice_id} received. Status: {invoice.processing_status.value}.",
        )
        return {
            "status": "accepted",
            "invoice_id": invoice.invoice_id,
            "processing_status": invoice.processing_status,
        }
    except UploadError as exc:
        await telegram.send_message(chat_id, f"Invoice rejected: {exc}.")
        return {"status": "rejected", "reason": str(exc)}
    except TelegramApiError as exc:
        raise HTTPException(status_code=502, detail="Telegram request failed") from exc
    except NotionApiError as exc:
        raise HTTPException(status_code=502, detail="Notion write failed") from exc
    finally:
        await telegram.close()


@router.get(
    "/api/invoices/{invoice_id}/status",
    response_model=InvoiceStatusResponse,
    summary="Read current invoice processing state",
)
async def invoice_status(
    request: Request,
    invoice_id: str,
    settings: Annotated[Settings, Depends(runtime_settings)],
) -> InvoiceStatusResponse:
    client = notion_client(request, settings)
    try:
        invoice = await client.get_invoice(invoice_id)
    except NotionApiError as exc:
        raise HTTPException(status_code=502, detail="Notion read failed") from exc
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice not found")
    return InvoiceStatusResponse(
        invoice_id=invoice.invoice_id,
        page_id=invoice.page_id,
        processing_status=invoice.processing_status,
        human_decision=invoice.human_decision,
        document_fingerprint=invoice.document_fingerprint,
        received_at=invoice.received_at,
        last_updated=invoice.last_updated,
    )
