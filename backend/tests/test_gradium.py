# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest

from atlas.adapters.gradium import GradiumSTT, GradiumTTS
from atlas.config import STTConfig, TTSConfig


class FakeWebSocket:
    def __init__(self, messages: list[dict[str, object]]) -> None:
        self.messages = messages
        self.sent: list[dict[str, object]] = []

    async def __aenter__(self) -> FakeWebSocket:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def send(self, value: str) -> None:
        self.sent.append(json.loads(value))

    async def recv(self) -> str:
        return json.dumps({"type": "ready", "sample_rate": 24000})

    def __aiter__(self) -> AsyncIterator[str]:
        async def messages() -> AsyncIterator[str]:
            for message in self.messages:
                yield json.dumps(message)

        return messages()


def tts_config() -> TTSConfig:
    return TTSConfig(
        provider="gradium",
        endpoint="wss://example.test",
        secret_env="GRADIUM_API_KEY",
        voice_id_env="GRADIUM_VOICE_ID",
        voice_id="voice",
        model="default",
        output_format="pcm_24000",
    )


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


@pytest.mark.asyncio
async def test_tts_does_not_synthesize_a_flush_marker_without_spoken_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GRADIUM_API_KEY", "test")
    websocket = FakeWebSocket([{"type": "end_of_stream"}])

    def fake_connect(*_args: object, **_kwargs: object) -> FakeWebSocket:
        return websocket

    monkeypatch.setattr("atlas.adapters.gradium.connect", fake_connect)
    tts = GradiumTTS(tts_config())

    async def no_text() -> AsyncIterator[str]:
        if False:
            yield ""

    async def audio(*_args: Any) -> None:
        return None

    await tts.stream_chunks(no_text(), "en", audio)

    submitted = [message.get("text") for message in websocket.sent if message.get("type") == "text"]
    assert submitted == []


@pytest.mark.asyncio
async def test_tts_control_markers_never_reach_subtitles(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRADIUM_API_KEY", "test")
    websocket = FakeWebSocket(
        [
            {"type": "text", "text": "Hello", "start_s": 0.0, "stop_s": 0.2},
            {"type": "text", "text": "<flush>", "start_s": 0.2, "stop_s": 0.2},
            {"type": "end_of_stream"},
        ]
    )

    def fake_connect(*_args: object, **_kwargs: object) -> FakeWebSocket:
        return websocket

    monkeypatch.setattr("atlas.adapters.gradium.connect", fake_connect)
    tts = GradiumTTS(tts_config())
    subtitles: list[str] = []

    async def one_phrase() -> AsyncIterator[str]:
        yield "Hello. "

    async def audio(*_args: Any) -> None:
        return None

    async def subtitle(text: str, _start: float, _stop: float) -> None:
        subtitles.append(text)

    await tts.stream_chunks(one_phrase(), "en", audio, subtitle)

    assert subtitles == ["Hello"]
