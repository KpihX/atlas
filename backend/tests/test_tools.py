from __future__ import annotations

import httpx
import pytest

from sidecar.adapters.exa import ExaSearch
from sidecar.adapters.jinko import JinkoFlights
from sidecar.config import ExaConfig, JinkoConfig


@pytest.mark.asyncio
async def test_exa_returns_bounded_grounded_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == "test"
        return httpx.Response(
            200,
            json={
                "requestId": "req",
                "results": [
                    {
                        "title": "Source",
                        "url": "https://example.com",
                        "publishedDate": "2026-01-01",
                        "highlights": ["Evidence"],
                    }
                ],
            },
        )

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    exa = ExaSearch(
        ExaConfig(enabled=True, endpoint="https://api.exa.ai/search", secret_env="EXA_API_KEY"),
        transport,
    )
    result = await exa.search({"query": "current evidence", "num_results": 3})
    assert result["status"] == "ok"
    assert result["results"][0]["url"] == "https://example.com"
    await transport.aclose()


@pytest.mark.asyncio
async def test_jinko_is_read_only_calendar_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JINKO_API_KEY", "test")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/flight_calendar"
        assert request.headers["x-api-key"] == "test"
        return httpx.Response(200, json={"routes": []})

    transport = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    jinko = JinkoFlights(
        JinkoConfig(enabled=True, base_url="https://api.gojinko.com", secret_env="JINKO_API_KEY"),
        transport,
    )
    result = await jinko.flight_calendar(
        {"origins": ["CDG"], "destinations": ["JFK"], "departure_date": "2026-10-01"}
    )
    assert result["status"] == "ok"
    assert result["calendar"] == {"routes": []}
    await transport.aclose()
