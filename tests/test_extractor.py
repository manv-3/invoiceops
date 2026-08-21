import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest

from app.config import Settings
from app.models.invoice import GeminiInvoiceExtraction
from app.services.extractor import ExtractionError, InvoiceExtractor


class FakeUploadedFile:
    uri = "file://test"
    mime_type = "application/pdf"


class FakeFiles:
    def upload(self, *, file: Path) -> FakeUploadedFile:
        assert file.exists()
        return FakeUploadedFile()


class FakeModels:
    def __init__(self, text: str) -> None:
        self.text = text

    def generate_content(self, **kwargs: object) -> object:
        assert kwargs["model"] == "test-model"
        assert "contents" in kwargs
        return type("Response", (), {"text": self.text})()


class FakeGemini:
    def __init__(self, text: str) -> None:
        self.files = FakeFiles()
        self.models = FakeModels(text)


class TransientError(RuntimeError):
    status_code = 503


class TransientModels(FakeModels):
    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.calls = 0

    def generate_content(self, **kwargs: object) -> object:
        self.calls += 1
        if self.calls == 1:
            raise TransientError("temporary provider failure")
        return super().generate_content(**kwargs)


class TransientGemini(FakeGemini):
    def __init__(self, text: str) -> None:
        self.files = FakeFiles()
        self.models = TransientModels(text)


def settings() -> Settings:
    return Settings(_env_file=None, gemini_api_key="test-key", gemini_model="test-model")


def test_extractor_returns_typed_values_and_null_unknown_fields(tmp_path: Path) -> None:
    document = tmp_path / "invoice.pdf"
    document.write_bytes(b"%PDF-test")
    payload = {
        "vendor_name": "Acme",
        "invoice_number": "A-1",
        "subtotal": "10.00",
        "tax": "1.80",
        "total": "11.80",
        "overall_confidence": "0.96",
        "field_confidence": {"invoice_number": "0.99"},
    }

    async def run():
        return await InvoiceExtractor(settings(), client=FakeGemini(json.dumps(payload))).extract(
            document
        )

    result = asyncio.run(run())
    assert result.vendor_name == "Acme"
    assert result.total == Decimal("11.80")
    assert result.gstin is None
    assert result.overall_confidence == Decimal("0.96")


def test_gemini_schema_does_not_emit_open_dictionary_properties() -> None:
    schema = GeminiInvoiceExtraction.model_json_schema()

    assert "additionalProperties" not in schema
    assert "additionalProperties" not in schema["properties"]["field_confidence"]


def test_malformed_model_output_is_reported(tmp_path: Path) -> None:
    document = tmp_path / "invoice.pdf"
    document.write_bytes(b"%PDF-test")

    async def run():
        return await InvoiceExtractor(settings(), client=FakeGemini("not-json")).extract(document)

    with pytest.raises(ExtractionError, match="structured output"):
        asyncio.run(run())


def test_transient_provider_error_is_retried(tmp_path: Path) -> None:
    document = tmp_path / "invoice.pdf"
    document.write_bytes(b"%PDF-test")
    payload = json.dumps({"vendor_name": "Acme", "overall_confidence": "0.9"})
    client = TransientGemini(payload)

    async def run():
        return await InvoiceExtractor(settings(), client=client).extract(document)

    result = asyncio.run(run())
    assert result.vendor_name == "Acme"
    assert client.models.calls == 2
