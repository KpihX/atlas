from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import monotonic
from typing import Any, cast

import httpx


def working_key() -> str:
    for line in Path("../.env").read_text().splitlines():
        if line.startswith("OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError("working OPENAI_API_KEY not found")


MESSAGES = [
    {
        "role": "system",
        "content": (
            "You are one unified live meeting collaborator. Never mention models or internal agents. "
            "Answer in French as natural spoken prose. Keep the answer under 45 words."
        ),
    },
    {
        "role": "user",
        "content": "Présente-toi et explique ce que tu peux faire pendant cette réunion.",
    },
]


async def run_trial(client: httpx.AsyncClient, api_key: str, trial: int) -> None:
    started = monotonic()
    first_text_ms: int | None = None
    parts: list[str] = []
    async with client.stream(
        "POST",
        "https://api.openai.com/v1/responses",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "gpt-5.6-luna",
            "input": MESSAGES,
            "reasoning": {"effort": "none"},
            "max_output_tokens": 160,
            "stream": True,
        },
    ) as response:
        if response.is_error:
            print(f"trial={trial} status={response.status_code}")
            return
        async for line in response.aiter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]":
                continue
            event_value: object = json.loads(line[6:])
            if not isinstance(event_value, dict):
                continue
            event = cast(dict[str, Any], event_value)
            if event.get("type") != "response.output_text.delta":
                continue
            delta = event.get("delta")
            if not isinstance(delta, str) or not delta:
                continue
            if first_text_ms is None:
                first_text_ms = round((monotonic() - started) * 1000)
            parts.append(delta)
    print(
        f"model=gpt-5.6-luna trial={trial} ttft_ms={first_text_ms} "
        f"total_ms={round((monotonic() - started) * 1000)} chars={len(''.join(parts))}"
    )


async def main() -> None:
    api_key = working_key()
    async with httpx.AsyncClient(timeout=45) as client:
        for trial in range(1, 4):
            await run_trial(client, api_key, trial)


if __name__ == "__main__":
    asyncio.run(main())
