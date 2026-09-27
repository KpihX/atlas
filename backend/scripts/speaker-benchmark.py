from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from time import monotonic
from typing import Any, cast

import httpx

from atlas.config import load_environment, secret


@dataclass(frozen=True)
class Candidate:
    name: str
    endpoint: str
    transport: str
    reasoning_style: str


CANDIDATES = (
    Candidate(
        "mimo-v2.6-flash-free",
        "https://opencode.ai/zen/v1/chat/completions",
        "chat",
        "thinking_type",
    ),
    Candidate(
        "mimo-v2.5-free",
        "https://opencode.ai/zen/v1/chat/completions",
        "chat",
        "thinking_type",
    ),
    Candidate(
        "deepseek-v4-flash",
        "https://opencode.ai/zen/v1/chat/completions",
        "chat",
        "reasoning_effort",
    ),
    Candidate(
        "deepseek-v4.1-flash",
        "https://opencode.ai/zen/v1/chat/completions",
        "chat",
        "reasoning_effort",
    ),
)

MESSAGES = [
    {
        "role": "system",
        "content": (
            "You are Atlas, the unified ambient collaborator in a live room. "
            "Never mention models, providers, "
            "internal agents, or architecture. Answer naturally in French. Return only JSON with keys "
            "speech and control. control must be none."
        ),
    },
    {
        "role": "user",
        "content": json.dumps(
            {
                "identity_name": "Atlas",
                "request": (
                    "Présente-toi et explique en une phrase ce que tu peux faire pendant cette réunion."
                ),
                "running_tasks": [],
                "available_capabilities": ["web research", "flight search", "living notes", "live board"],
            },
            ensure_ascii=False,
        ),
    },
]


def payload(candidate: Candidate) -> dict[str, object]:
    if candidate.transport == "responses":
        return {
            "model": candidate.name,
            "input": MESSAGES,
            "max_output_tokens": 256,
            "reasoning": {"effort": "none"},
        }
    result: dict[str, object] = {
        "model": candidate.name,
        "messages": MESSAGES,
        "max_completion_tokens": 256,
        "temperature": 0.3,
    }
    if candidate.reasoning_style == "thinking_type":
        result["thinking"] = {"type": "disabled"}
    elif candidate.reasoning_style == "reasoning_effort":
        result["reasoning_effort"] = "none"
    return result


def content(raw: dict[str, Any], candidate: Candidate) -> str:
    if candidate.transport == "chat":
        choices = cast(list[dict[str, Any]], raw.get("choices", []))
        if not choices:
            return ""
        message = cast(dict[str, Any], choices[0].get("message", {}))
        value = message.get("content", "")
        return value if isinstance(value, str) else ""
    direct = raw.get("output_text")
    if isinstance(direct, str):
        return direct
    parts: list[str] = []
    for item in cast(list[dict[str, Any]], raw.get("output", [])):
        for part in cast(list[dict[str, Any]], item.get("content", [])):
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
    return "\n".join(parts)


async def benchmark(candidate: Candidate, api_key: str, trials: int = 3) -> None:
    async with httpx.AsyncClient(timeout=45) as client:
        for trial in range(1, trials + 1):
            started = monotonic()
            response = await client.post(
                candidate.endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload(candidate),
            )
            elapsed_ms = round((monotonic() - started) * 1000)
            if response.is_error:
                try:
                    body = response.json()
                except json.JSONDecodeError:
                    body = {}
                detail = body.get("error", {}) if isinstance(body, dict) else {}
                print(
                    f"model={candidate.name} trial={trial} status={response.status_code} "
                    f"type={detail.get('type', 'unknown')} code={detail.get('code', 'unknown')} "
                    f"message={str(detail.get('message', 'unknown'))[:180]!r} elapsed_ms={elapsed_ms}"
                )
                break
            raw_value: object = response.json()
            raw = cast(dict[str, Any], raw_value) if isinstance(raw_value, dict) else {}
            text = content(raw, candidate)
            valid_json = False
            try:
                decoded = json.loads(text)
                valid_json = isinstance(decoded, dict) and isinstance(decoded.get("speech"), str)
            except json.JSONDecodeError:
                pass
            print(
                f"model={candidate.name} trial={trial} status=200 elapsed_ms={elapsed_ms} "
                f"valid_json={str(valid_json).lower()} chars={len(text)}"
            )


async def benchmark_stream(candidate: Candidate, api_key: str, trials: int = 3) -> None:
    if candidate.transport != "chat":
        return
    async with httpx.AsyncClient(timeout=45) as client:
        for trial in range(1, trials + 1):
            request_payload = payload(candidate) | {"stream": True}
            started = monotonic()
            first_text_ms: int | None = None
            parts: list[str] = []
            async with client.stream(
                "POST",
                candidate.endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=request_payload,
            ) as response:
                if response.is_error:
                    print(f"stream_model={candidate.name} status={response.status_code}")
                    return
                async for line in response.aiter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    event_value: object = json.loads(line[6:])
                    if not isinstance(event_value, dict):
                        continue
                    event = cast(dict[str, Any], event_value)
                    choices = cast(list[dict[str, Any]], event.get("choices", []))
                    if not choices:
                        continue
                    delta = cast(dict[str, Any], choices[0].get("delta", {}))
                    token = delta.get("content")
                    if isinstance(token, str) and token:
                        if first_text_ms is None:
                            first_text_ms = round((monotonic() - started) * 1000)
                        parts.append(token)
            total_ms = round((monotonic() - started) * 1000)
            print(
                f"stream_model={candidate.name} trial={trial} status=200 "
                f"ttft_ms={first_text_ms} total_ms={total_ms} chars={len(''.join(parts))}"
            )


async def main() -> None:
    load_environment()
    api_key = secret("OPENCODE_ZEN_API_KEY")
    if api_key is None:
        raise RuntimeError("OPENCODE_ZEN_API_KEY is not configured")
    for candidate in CANDIDATES:
        await benchmark(candidate, api_key)
        if candidate.name.startswith("deepseek"):
            await benchmark_stream(candidate, api_key)


if __name__ == "__main__":
    asyncio.run(main())
