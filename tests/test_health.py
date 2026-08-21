import asyncio

import httpx

from app.main import app


def test_health_endpoint_returns_service_identity() -> None:
    async def request_health() -> httpx.Response:
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.get("/health")

    response = asyncio.run(request_health())

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "invoiceops",
        "version": "0.1.0",
    }
