"""Invoice upload, status, and review endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.config import Settings, get_settings
from app.models.invoice import (
    InvoiceRecord,
    InvoiceStatus,
    ReviewDecision,
    ReviewRequest,
)
from app.services import notion_client, store
from app.services.extraction import extract_invoice
from app.services.run_log import log_event
from app.services.validation import validate_invoice
from app.services.workflow import IllegalTransitionError, transition
from app.utils.fingerprint import compute_fingerprint
from app.utils.storage import ALLOWED_CONTENT_TYPES, safe_extension, store_document

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/invoices", tags=["invoices"])


@router.post(
    "/upload",
    response_model=InvoiceRecord,
    status_code=201,
    summary="Upload an invoice",
)
async def upload_invoice(
    file: Annotated[UploadFile, File(...)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> InvoiceRecord:
    """Accept an invoice document and run the intake workflow."""

    # --- 1. File-type and size validation ---
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {file.content_type}")

    content = await file.read()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds {settings.max_upload_mb} MB limit.",
        )

    original_filename = file.filename or "unknown"
    try:
        safe_extension(original_filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # --- 2. Store and fingerprint ---
    incoming_dir = settings.storage_dir / "incoming"
    stored_path = store_document(content, original_filename, incoming_dir)
    fingerprint = compute_fingerprint(content)

    record = InvoiceRecord(
        original_filename=original_filename,
        stored_path=stored_path,
        fingerprint=fingerprint,
    )
    store.save(record)
    await log_event(
        record.id,
        "intake",
        f"Received {original_filename}, fingerprint={fingerprint[:12]}…",
    )

    # --- 3. Extraction ---
    try:
        record = transition(record, InvoiceStatus.EXTRACTING)
        store.save(record)
        await log_event(record.id, "extraction_start")

        extraction = await extract_invoice(content, file.content_type or "application/octet-stream")
        record = record.model_copy(update={"extraction": extraction})
        record = transition(record, InvoiceStatus.EXTRACTED)
        store.save(record)
        await log_event(
            record.id,
            "extraction_complete",
            f"confidence={extraction.confidence:.2f}",
        )
    except IllegalTransitionError:
        raise
    except Exception as exc:
        record = transition(record, InvoiceStatus.FAILED)
        store.save(record)
        await log_event(record.id, "extraction_failed", str(exc))
        raise HTTPException(status_code=500, detail="Extraction failed.") from exc

    # --- 4. Validation ---
    try:
        record = transition(record, InvoiceStatus.VALIDATING)
        store.save(record)
        await log_event(record.id, "validation_start")

        validation = validate_invoice(record, extraction)
        record = record.model_copy(update={"validation": validation})

        if validation.needs_review:
            record = transition(record, InvoiceStatus.NEEDS_REVIEW)
            await log_event(
                record.id,
                "needs_review",
                f"{len(validation.findings)} finding(s)",
            )
        else:
            record = transition(record, InvoiceStatus.VALIDATED)
            await log_event(record.id, "validation_passed")

            # Auto-approve clean invoices
            if settings.auto_process_clean_invoices:
                record = transition(record, InvoiceStatus.APPROVED)
                await log_event(record.id, "auto_approved", "Clean invoice auto-approved.")
    except IllegalTransitionError:
        raise
    except Exception as exc:
        record = transition(record, InvoiceStatus.FAILED)
        store.save(record)
        await log_event(record.id, "validation_failed", str(exc))
        raise HTTPException(status_code=500, detail="Validation failed.") from exc

    store.save(record)

    # --- 5. Notion ---
    try:
        page_id = await notion_client.create_invoice_page(record)
        record = record.model_copy(update={"notion_page_id": page_id})
        store.save(record)
    except Exception:
        logger.exception("Failed to create Notion page for %s", record.id)

    return record


@router.get(
    "/{invoice_id}",
    response_model=InvoiceRecord,
    summary="Get invoice status",
)
async def get_invoice(invoice_id: str) -> InvoiceRecord:
    """Return the current state of a single invoice."""
    record = store.get(invoice_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return record


@router.get("/", response_model=list[InvoiceRecord], summary="List all invoices")
async def list_invoices() -> list[InvoiceRecord]:
    """Return all invoice records."""
    return store.all_records()


@router.post(
    "/{invoice_id}/review",
    response_model=InvoiceRecord,
    summary="Submit review decision",
)
async def review_invoice(invoice_id: str, body: ReviewRequest) -> InvoiceRecord:
    """Submit a human review decision for an invoice in NEEDS_REVIEW status."""
    record = store.get(invoice_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Invoice not found.")

    if record.status != InvoiceStatus.NEEDS_REVIEW:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Invoice is in '{record.status.value}' status; review requires 'needs_review'."
            ),
        )

    if body.decision == "override" and not body.reviewer_note:
        raise HTTPException(status_code=400, detail="Override decisions require a reviewer note.")

    decision = ReviewDecision(
        decision=body.decision,
        reviewer_note=body.reviewer_note,
        overrides=body.overrides,
    )
    record = record.model_copy(update={"review_decision": decision})

    if body.decision in ("approve", "override"):
        record = transition(record, InvoiceStatus.APPROVED)
        await log_event(
            record.id,
            "reviewer_approved",
            f"Decision: {body.decision}, note: {body.reviewer_note or '—'}",
        )
    else:
        record = transition(record, InvoiceStatus.REJECTED)
        await log_event(
            record.id,
            "reviewer_rejected",
            f"Note: {body.reviewer_note or '—'}",
        )

    store.save(record)

    # Update Notion
    if record.notion_page_id:
        try:
            await notion_client.update_invoice_page(
                record.notion_page_id,
                {"Status": {"select": {"name": record.status.value}}},
            )
        except Exception:
            logger.exception("Failed to update Notion page for %s", record.id)

    return record
