from __future__ import annotations

from typing import Any

import httpx

from sidecar.config import JinkoConfig, secret


class JinkoFlights:
    def __init__(self, config: JinkoConfig, transport: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._secret = secret(config.secret_env)
        self._transport = transport or httpx.AsyncClient(timeout=20)
        self._owns_transport = transport is None

    @property
    def available(self) -> bool:
        return self.config.enabled and self._secret is not None

    async def close(self) -> None:
        if self._owns_transport:
            await self._transport.aclose()

    async def flight_calendar(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            return {"status": "unavailable", "reason": "Jinko is not configured"}
        payload = {
            "origins": arguments.get("origins", []),
            "destinations": arguments.get("destinations", []),
            "trip_type": "oneway",
            "departure_dates": [arguments.get("departure_date", "")],
            "adults": 1,
            "currency": arguments.get("currency", "EUR"),
        }
        response = await self._transport.post(
            f"{self.config.base_url.rstrip('/')}/v1/flight_calendar",
            headers={"X-API-Key": str(self._secret), "Content-Type": "application/json"},
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise TypeError("Jinko calendar response must be an object")
        return {"status": "ok", "freshness": "provider-dependent", "calendar": data}
