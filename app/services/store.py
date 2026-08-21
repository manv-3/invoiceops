"""In-memory invoice store.

A production deployment would replace this with a persistent database.  For the
current single-process implementation this dict-backed store is sufficient.
"""

from __future__ import annotations

from app.models.invoice import InvoiceRecord

_invoices: dict[str, InvoiceRecord] = {}
_fingerprints: dict[str, str] = {}  # fingerprint -> invoice_id


def save(record: InvoiceRecord) -> None:
    """Save or update an invoice record."""
    _invoices[record.id] = record
    _fingerprints[record.fingerprint] = record.id


def get(invoice_id: str) -> InvoiceRecord | None:
    """Return the record for *invoice_id*, or ``None``."""
    return _invoices.get(invoice_id)


def get_by_fingerprint(fingerprint: str) -> InvoiceRecord | None:
    """Return the record that shares *fingerprint*, or ``None``."""
    inv_id = _fingerprints.get(fingerprint)
    if inv_id is None:
        return None
    return _invoices.get(inv_id)


def find_by_vendor_and_number(
    vendor_name: str | None, invoice_number: str | None
) -> InvoiceRecord | None:
    """Return the first record matching vendor and invoice number, or ``None``."""
    if vendor_name is None or invoice_number is None:
        return None
    for record in _invoices.values():
        if record.extraction is None:
            continue
        data = record.extraction.data
        if data.vendor_name == vendor_name and data.invoice_number == invoice_number:
            return record
    return None


def list_by_status(status: str) -> list[InvoiceRecord]:
    """Return all records with the given status value."""
    return [r for r in _invoices.values() if r.status.value == status]


def all_records() -> list[InvoiceRecord]:
    """Return all invoice records."""
    return list(_invoices.values())


def clear() -> None:
    """Remove all records.  Intended for testing."""
    _invoices.clear()
    _fingerprints.clear()
