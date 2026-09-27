from __future__ import annotations

from typing import Any, cast

import httpx

from atlas.config import ExaConfig, secret


class ExaSearch:
    def __init__(self, config: ExaConfig, transport: httpx.AsyncClient | None = None) -> None:
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

    async def search(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.available:
            return {"status": "unavailable", "reason": "Exa is not configured"}
        query = str(arguments.get("query", "")).strip()
        if not query:
            raise ValueError("Exa search requires a non-empty query")
        requested = arguments.get("num_results", self.config.num_results)
        num_results = min(max(int(requested), 1), 10)
        response = await self._transport.post(
            self.config.endpoint,
            headers={"x-api-key": str(self._secret), "Content-Type": "application/json"},
            json={
                "query": query,
                "type": self.config.search_type,
                "numResults": num_results,
                "contents": {"highlights": True},
            },
        )
        response.raise_for_status()
        raw_value: object = response.json()
        if not isinstance(raw_value, dict):
            raise TypeError("Exa search response must contain a results array")
        raw = cast(dict[str, Any], raw_value)
        if not isinstance(raw.get("results"), list):
            raise TypeError("Exa search response must contain a results array")
        results: list[dict[str, Any]] = []
        raw_results = cast(list[Any], raw["results"])
        for value in raw_results[:num_results]:
            if not isinstance(value, dict):
                continue
            item = cast(dict[str, Any], value)
            results.append(
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "published_date": item.get("publishedDate"),
                    "author": item.get("author"),
                    "highlights": item.get("highlights", []),
                }
            )
        return {
            "status": "ok",
            "request_id": raw.get("requestId"),
            "results": results,
            "cost": raw.get("costDollars"),
        }
