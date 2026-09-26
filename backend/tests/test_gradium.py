from __future__ import annotations

import asyncio

import pytest

from sidecar.adapters.gradium import GradiumSTT
from sidecar.config import STTConfig


@pytest.mark.asyncio
async def test_stt_accumulates_fragments_until_flush(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRADIUM_API_KEY", "test")
    stt = GradiumSTT(
        STTConfig(
            provider="gradium",
            endpoint="wss://example.test",
            secret_env="GRADIUM_API_KEY",
            model="default",
            input_format="pcm_24000",
            language="fr",
            delay_in_frames=16,
        )
    )
    partials: list[str] = []
    finals: list[str] = []
    events: list[str] = []

    async def partial(text: str) -> None:
        partials.append(text)

    async def final(text: str) -> None:
        finals.append(text)

    async def event(kind: str, _: dict[str, object]) -> None:
        events.append(kind)

    stt._on_partial = partial
    stt._on_final = final
    stt._on_event = event
    await stt._handle_message({"type": "text", "text": "On va"})
    await stt._handle_message({"type": "end_text", "stop_s": 0.4})
    await stt._handle_message({"type": "text", "text": "lancer une nouvelle session"})
    assert finals == []
    assert partials[-1] == "On va lancer une nouvelle session"
    await stt._handle_message({"type": "flushed", "flush_id": 1})
    await asyncio.sleep(0.2)
    assert finals == ["On va lancer une nouvelle session"]
    assert events == ["text", "end_text", "text", "flushed"]
