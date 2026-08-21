"""Invoice data extraction using Google GenAI.

When ``dev_mode`` is enabled the service returns plausible mock data instead of
calling the Gemini API.  This allows the full workflow to be exercised without
provider credentials.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from app.config import get_settings
from app.models.invoice import ExtractionResult, InvoiceData, LineItem

logger = logging.getLogger(__name__)


def _mock_extraction(file_bytes: bytes) -> ExtractionResult:
    """Return a deterministic mock extraction for development."""
    return ExtractionResult(
        data=InvoiceData(
            vendor_name="Acme Corp",
            vendor_id="ACME-001",
            invoice_number="INV-2024-0042",
            invoice_date="2024-06-15",
            due_date="2024-07-15",
            currency="USD",
            subtotal=Decimal("1000.00"),
            tax=Decimal("100.00"),
            total=Decimal("1100.00"),
            purchase_order_ref="PO-12345",
            payment_details="Bank: Example Bank, Account: 123456789",
            line_items=[
                LineItem(
                    description="Consulting services",
                    quantity=Decimal("10"),
                    unit_price=Decimal("100.00"),
                    amount=Decimal("1000.00"),
                ),
            ],
        ),
        confidence=0.95,
        field_confidences={
            "vendor_name": 0.98,
            "invoice_number": 0.97,
            "total": 0.96,
            "subtotal": 0.95,
            "tax": 0.93,
        },
        raw_text="[mock extraction – dev mode]",
    )


async def extract_invoice(file_bytes: bytes, content_type: str) -> ExtractionResult:
    """Extract structured invoice data from *file_bytes*.

    Uses Google GenAI when credentials are available and ``dev_mode`` is off.
    Falls back to mock data in dev mode.
    """
    settings = get_settings()

    if settings.dev_mode or settings.gemini_api_key is None:
        logger.info("Using mock extraction (dev_mode=%s)", settings.dev_mode)
        return _mock_extraction(file_bytes)

    # --- Real extraction via Google GenAI ---
    from google import genai  # type: ignore[import-untyped]

    client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())

    prompt = (
        "Extract all invoice fields from this document.  Return a JSON object with "
        "these fields: vendor_name, vendor_id, invoice_number, invoice_date, due_date, "
        "currency, subtotal, tax, total, purchase_order_ref, payment_details, "
        "line_items (array of objects with description, quantity, unit_price, amount).  "
        "Use null for any field you cannot determine.  Return ONLY valid JSON."
    )

    mime_map = {
        "application/pdf": "application/pdf",
        "image/png": "image/png",
        "image/jpeg": "image/jpeg",
    }
    mime = mime_map.get(content_type, "application/octet-stream")

    response = client.models.generate_content(
        model=settings.gemini_model or "gemini-2.0-flash",
        contents=[
            {
                "parts": [
                    {"text": prompt},
                    {"inline_data": {"mime_type": mime, "data": file_bytes}},
                ],
            },
        ],
    )

    raw_text = response.text or ""

    # Strip markdown code fences if the model wrapped the JSON.
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = lines[1:]  # remove opening fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines)

    parsed = json.loads(cleaned)

    line_items: list[LineItem] = []
    for item in parsed.get("line_items", []) or []:
        line_items.append(
            LineItem(
                description=item.get("description"),
                quantity=(
                    Decimal(str(item["quantity"])) if item.get("quantity") is not None else None
                ),
                unit_price=(
                    Decimal(str(item["unit_price"])) if item.get("unit_price") is not None else None
                ),
                amount=(Decimal(str(item["amount"])) if item.get("amount") is not None else None),
            )
        )

    data = InvoiceData(
        vendor_name=parsed.get("vendor_name"),
        vendor_id=parsed.get("vendor_id"),
        invoice_number=parsed.get("invoice_number"),
        invoice_date=parsed.get("invoice_date"),
        due_date=parsed.get("due_date"),
        currency=parsed.get("currency"),
        subtotal=(Decimal(str(parsed["subtotal"])) if parsed.get("subtotal") is not None else None),
        tax=Decimal(str(parsed["tax"])) if parsed.get("tax") is not None else None,
        total=(Decimal(str(parsed["total"])) if parsed.get("total") is not None else None),
        purchase_order_ref=parsed.get("purchase_order_ref"),
        payment_details=parsed.get("payment_details"),
        line_items=line_items,
    )

    return ExtractionResult(
        data=data,
        confidence=0.85,
        field_confidences={},
        raw_text=raw_text,
    )
