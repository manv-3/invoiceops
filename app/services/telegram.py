"""Small Telegram Bot API adapter for invoice document intake."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx


class TelegramApiError(RuntimeError):
    """Raised when Telegram rejects an API request or returns an invalid response."""


class TelegramClient:
    def __init__(
        self,
        token: str,
        api_base_url: str = "https://api.telegram.org",
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._token = token
        self._api_base_url = api_base_url.rstrip("/")
        self._owns_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(30.0))

    async def close(self) -> None:
        if self._owns_client:
            await self._http_client.aclose()

    async def get_file_path(self, file_id: str) -> str:
        result = await self._api_request("getFile", {"file_id": file_id})
        file_path = result.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            raise TelegramApiError("Telegram file response did not include a file path")
        return file_path

    async def download_file(self, file_path: str) -> bytes:
        try:
            response = await self._http_client.get(
                f"{self._api_base_url}/file/bot{self._token}/{file_path}"
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TelegramApiError("Telegram document download failed") from exc
        return response.content

    async def send_message(self, chat_id: int | str, text: str) -> None:
        await self._api_request("sendMessage", {"chat_id": chat_id, "text": text})

    async def _api_request(self, method: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        try:
            response = await self._http_client.post(
                f"{self._api_base_url}/bot{self._token}/{method}", json=payload
            )
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise TelegramApiError(f"Telegram {method} request failed") from exc
        if not isinstance(body, Mapping) or body.get("ok") is not True:
            raise TelegramApiError(f"Telegram {method} request was not accepted")
        result = body.get("result")
        if not isinstance(result, Mapping):
            raise TelegramApiError(f"Telegram {method} response was malformed")
        return dict(result)


def extract_document_reference(update: Mapping[str, Any]) -> tuple[int | str, str, str, str] | None:
    """Return chat ID, Telegram file ID, safe filename, and MIME type from an update."""

    message = update.get("message")
    if not isinstance(message, Mapping):
        return None
    chat = message.get("chat")
    chat_id = chat.get("id") if isinstance(chat, Mapping) else None
    if not isinstance(chat_id, (int, str)):
        return None

    document = message.get("document")
    if isinstance(document, Mapping) and isinstance(document.get("file_id"), str):
        file_name = document.get("file_name")
        mime_type = document.get("mime_type")
        safe_name = file_name if isinstance(file_name, str) else "telegram-invoice.pdf"
        safe_mime = mime_type if isinstance(mime_type, str) else "application/pdf"
        return chat_id, document["file_id"], safe_name, safe_mime

    photos = message.get("photo")
    if isinstance(photos, list):
        candidates = [item for item in photos if isinstance(item, Mapping)]
        if candidates and isinstance(candidates[-1].get("file_id"), str):
            return chat_id, candidates[-1]["file_id"], "telegram-invoice.jpg", "image/jpeg"
    return None
