import asyncio
from collections.abc import Callable

import httpx

from app.services.telegram import TelegramClient, extract_document_reference


def test_extract_document_reference_supports_document_and_photo() -> None:
    document = {
        "message": {
            "chat": {"id": 42},
            "document": {
                "file_id": "file-1",
                "file_name": "untrusted.pdf",
                "mime_type": "application/pdf",
            },
        }
    }
    photo = {
        "message": {
            "chat": {"id": 42},
            "photo": [{"file_id": "small"}, {"file_id": "large"}],
        }
    }

    assert extract_document_reference(document) == (
        42,
        "file-1",
        "untrusted.pdf",
        "application/pdf",
    )
    assert extract_document_reference(photo) == (42, "large", "telegram-invoice.jpg", "image/jpeg")


def client_for(handler: Callable[[httpx.Request], httpx.Response]) -> TelegramClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="https://telegram.test")
    return TelegramClient("bot-token", http_client=http_client)


def test_telegram_client_downloads_and_sends_without_exposing_token() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/getFile"):
            return httpx.Response(
                200, json={"ok": True, "result": {"file_path": "documents/a.pdf"}}
            )
        if request.url.path == "/file/botbot-token/documents/a.pdf":
            return httpx.Response(200, content=b"%PDF-fixture")
        assert request.url.path.endswith("/sendMessage")
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    async def run() -> bytes:
        client = client_for(handler)
        try:
            path = await client.get_file_path("file-1")
            content = await client.download_file(path)
            await client.send_message(42, "received")
            return content
        finally:
            await client.close()

    assert asyncio.run(run()) == b"%PDF-fixture"
    assert calls == [
        "/botbot-token/getFile",
        "/file/botbot-token/documents/a.pdf",
        "/botbot-token/sendMessage",
    ]
