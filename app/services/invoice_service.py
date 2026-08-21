"""Invoice intake service: bounded storage, fingerprinting, and initial audit writes."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from fastapi import UploadFile

from app.config import Settings
from app.models.invoice import InvoiceRecord, RunLogEntry, new_invoice_id, utc_now

MAX_READ_CHUNK = 1024 * 1024
ALLOWED_MIME_TYPES: dict[str, str] = {
    "application/pdf": ".pdf",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}


class UploadError(ValueError):
    """A user-correctable upload validation error."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class InvoiceServiceClient(Protocol):
    async def create_invoice(self, invoice: InvoiceRecord) -> InvoiceRecord: ...

    async def create_run_log(
        self, entry: RunLogEntry, invoice_page_id: str | None = None
    ) -> RunLogEntry: ...

    async def get_invoice(self, invoice_id: str) -> InvoiceRecord | None: ...

    async def attach_invoice_file(self, invoice_id: str, file_path: Path) -> InvoiceRecord: ...


class InvoiceIntakeService:
    def __init__(self, notion: InvoiceServiceClient, settings: Settings) -> None:
        self.notion = notion
        self.settings = settings

    async def ingest(self, upload: UploadFile) -> InvoiceRecord:
        extension = ALLOWED_MIME_TYPES.get(upload.content_type or "")
        if extension is None:
            raise UploadError("unsupported invoice MIME type", 415)

        invoice_id = new_invoice_id()
        incoming_dir = self.settings.storage_dir / "incoming"
        incoming_dir.mkdir(parents=True, exist_ok=True)
        temporary_path = incoming_dir / f".{invoice_id}-{uuid4().hex}.part"
        final_path = incoming_dir / f"{invoice_id}{extension}"
        max_bytes = self.settings.max_upload_mb * 1024 * 1024
        digest = hashlib.sha256()
        size = 0
        prefix = bytearray()

        try:
            with temporary_path.open("wb") as destination:
                while True:
                    chunk = await upload.read(MAX_READ_CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadError(
                            f"invoice exceeds the {self.settings.max_upload_mb} MB limit", 413
                        )
                    digest.update(chunk)
                    if len(prefix) < 16:
                        prefix.extend(chunk[: 16 - len(prefix)])
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if not _matches_file_signature(extension, bytes(prefix)):
                raise UploadError("file content does not match its declared type", 415)
            os.replace(temporary_path, final_path)
        except UploadError:
            temporary_path.unlink(missing_ok=True)
            raise
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            raise RuntimeError("could not persist invoice document") from exc

        now = utc_now()
        record = InvoiceRecord(
            invoice_id=invoice_id,
            document_fingerprint=digest.hexdigest(),
            source="Upload",
            received_at=now,
            last_updated=now,
        )
        created = await self.notion.create_invoice(record)
        attach_file = getattr(self.notion, "attach_invoice_file", None)
        if attach_file is not None:
            try:
                created = await attach_file(created.invoice_id, final_path)
            except Exception as exc:
                await self.notion.create_run_log(
                    RunLogEntry(
                        invoice_id=created.invoice_id,
                        invoice_page_id=created.page_id,
                        trigger="Upload",
                        stage="Intake",
                        result="Failed",
                        summary="Stored document but failed to attach Source File in Notion",
                        error_message=f"{type(exc).__name__}: {str(exc)[:240]}",
                        service_version=self.settings.service_version,
                    ),
                    created.page_id,
                )
                raise
        await self.notion.create_run_log(
            RunLogEntry(
                invoice_id=created.invoice_id,
                invoice_page_id=created.page_id,
                trigger="Upload",
                stage="Intake",
                result="Success",
                summary=f"Stored original invoice document ({size} bytes)",
                duration_ms=0,
                service_version=self.settings.service_version,
            ),
            created.page_id,
        )
        return created


def stored_document_path(settings: Settings, invoice_id: str) -> Path:
    """Resolve a stored document using only the generated internal ID."""

    incoming_dir = settings.storage_dir / "incoming"
    matches = sorted(incoming_dir.glob(f"{invoice_id}.*"))
    if not matches:
        raise FileNotFoundError(f"stored document for {invoice_id} was not found")
    return matches[0]


def _matches_file_signature(extension: str, prefix: bytes) -> bool:
    signatures = {
        ".pdf": lambda value: value.startswith(b"%PDF-"),
        ".png": lambda value: value.startswith(b"\x89PNG\r\n\x1a\n"),
        ".jpg": lambda value: value.startswith(b"\xff\xd8\xff"),
    }
    return signatures[extension](prefix)
