from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    effect: Literal["read", "local_write", "external_write"]
    handler: ToolHandler


class ToolRegistry:
    def __init__(self, max_concurrency: int) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._semaphore = asyncio.Semaphore(max_concurrency)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"duplicate tool: {spec.name}")
        self._tools[spec.name] = spec

    def prompt_catalog(self) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "description": item.description,
                "input_schema": item.input_schema,
                "effect": item.effect,
            }
            for item in self._tools.values()
        ]

    async def execute(
        self, name: str, arguments: dict[str, Any], *, approved: bool = False
    ) -> dict[str, Any]:
        spec = self._tools.get(name)
        if spec is None:
            raise ValueError(f"unknown tool: {name}")
        if spec.effect == "external_write" and not approved:
            return {"status": "approval_required", "tool": name, "arguments": arguments}
        async with self._semaphore:
            return await spec.handler(arguments)
