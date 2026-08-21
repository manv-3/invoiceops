"""Gemini document extraction behind a typed, decision-free interface."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types
from pydantic import ValidationError

from app.config import Settings
from app.models.invoice import GeminiInvoiceExtraction, InvoiceExtraction

EXTRACTION_PROMPT = """Extract invoice data from the attached document into the supplied
JSON schema.

Rules:
- Extract only values directly supported by the document.
- Return null for every unknown, missing, or unreadable value. Never guess.
- Preserve invoice numbers and GSTIN exactly as printed.
- Preserve the document's currency.
- Return numeric monetary values without currency symbols.
- Return dates as ISO 8601 dates when clearly supported.
- Return line items only when they are present and readable.
- Return confidence values on a 0 to 1 scale where possible.
- This is extraction only. Do not decide whether arithmetic is valid, whether the vendor is
  trusted, whether an invoice is a duplicate, or whether workflow should continue.
I will parse this response programmatically, so return JSON only.
"""


class ExtractionError(RuntimeError):
    """Raised when Gemini cannot provide valid typed extraction output."""


class InvoiceExtractor:
    """Asynchronous adapter around the current Google GenAI Python SDK."""

    def __init__(self, settings: Settings, client: Any | None = None) -> None:
        self.settings = settings
        self.client = client

    async def extract(self, file_path: Path) -> InvoiceExtraction:
        if not file_path.is_file():
            raise ExtractionError("invoice document does not exist")
        if self.settings.gemini_api_key is None or not self.settings.gemini_model:
            raise ExtractionError("Gemini extraction is not configured")
        return await asyncio.to_thread(self._extract_sync, file_path)

    def _extract_sync(self, file_path: Path) -> InvoiceExtraction:
        client = self.client or genai.Client(
            api_key=self.settings.gemini_api_key.get_secret_value()
        )
        response = None
        for attempt in range(3):
            try:
                uploaded_file = client.files.upload(file=file_path)
                response = client.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=[EXTRACTION_PROMPT, uploaded_file],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=GeminiInvoiceExtraction,
                        thinking_config=types.ThinkingConfig(thinking_level="minimal"),
                        max_output_tokens=8192,
                    ),
                )
                break
            except Exception as exc:
                if attempt == 2 or not _is_transient_provider_error(exc):
                    raise ExtractionError("Gemini extraction request failed") from exc
                time.sleep(0.5 * (2**attempt))
        if response is None:
            raise ExtractionError("Gemini extraction request failed")

        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, GeminiInvoiceExtraction):
            return InvoiceExtraction.model_validate(parsed.model_dump())
        if isinstance(parsed, InvoiceExtraction):
            return parsed
        raw_text = getattr(response, "text", None)
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ExtractionError("Gemini returned no structured output")
        try:
            return InvoiceExtraction.model_validate_json(raw_text)
        except (ValidationError, ValueError) as exc:
            raise ExtractionError("Gemini returned invalid structured output") from exc


def _is_transient_provider_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    return status_code in {429, 500, 502, 503, 504} or type(error).__name__ in {
        "ServerError",
        "GatewayTimeoutError",
    }
