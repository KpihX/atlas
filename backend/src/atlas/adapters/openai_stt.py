from __future__ import annotations

import asyncio
import base64
import json
from contextlib import suppress
from typing import Any, cast

from websockets.asyncio.client import ClientConnection, connect

from atlas.config import STTModelConfig, secret
from atlas.core.ports import EventCallback, TextCallback


def _message(raw: str | bytes) -> dict[str, Any]:
    value: object = json.loads(raw.decode() if isinstance(raw, bytes) else raw)
    if not isinstance(value, dict):
        raise TypeError("OpenAI realtime message must be an object")
    return cast(dict[str, Any], value)


class OpenAISTT:
    def __init__(self, config: STTModelConfig) -> None:
        self.config = config
        self._secret = secret(config.secret_env)
        self._ws: ClientConnection | None = None
        self._consumer: asyncio.Task[None] | None = None
        self._on_partial: TextCallback | None = None
        self._on_final: TextCallback | None = None
        self._on_event: EventCallback | None = None
        self._partials: dict[str, str] = {}
        self._error: RuntimeError | None = None
        self._audio_bytes = 0

    @property
    def available(self) -> bool:
        return self._secret is not None

    @property
    def connected(self) -> bool:
        return self._ws is not None and self._error is None

    @property
    def rotate_after_seconds(self) -> float:
        return self.config.rotate_after_seconds

    async def start(
        self,
        on_partial: TextCallback,
        on_final: TextCallback,
        on_event: EventCallback,
        language: str,
    ) -> None:
        self._on_partial, self._on_final, self._on_event = on_partial, on_final, on_event
        if self._secret is None or self._ws is not None:
            return
        self._error = None
        self._ws = await connect(
            self.config.endpoint,
            additional_headers={"Authorization": f"Bearer {self._secret}"},
        )
        await self._ws.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "transcription",
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": {
                                    "model": self.config.model,
                                    "language": language or self.config.language,
                                },
                                "noise_reduction": {"type": self.config.noise_reduction},
                                "turn_detection": {
                                    "type": "server_vad",
                                    "prefix_padding_ms": 300,
                                    "silence_duration_ms": self.config.silence_duration_ms,
                                },
                            }
                        },
                    },
                }
            )
        )
        while True:
            event = _message(await self._ws.recv())
            kind = str(event.get("type", "unknown"))
            await self._emit(kind, event)
            if kind == "session.updated":
                break
            if kind == "error":
                await self.stop()
                raise RuntimeError(self._error_detail(event))
        self._consumer = asyncio.create_task(self._consume())

    async def send(self, audio: bytes) -> None:
        if self._error is not None:
            raise self._error
        if self._ws is None:
            return
        await self._ws.send(
            json.dumps({"type": "input_audio_buffer.append", "audio": base64.b64encode(audio).decode()})
        )
        self._audio_bytes += len(audio)

    async def flush(self) -> None:
        if self._ws is not None and self._audio_bytes >= 4800:
            await self._ws.send(json.dumps({"type": "input_audio_buffer.commit"}))
            self._audio_bytes = 0

    async def stop(self) -> None:
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.close()
            self._ws = None
        if self._consumer is not None:
            self._consumer.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._consumer
            self._consumer = None
        self._partials.clear()
        self._audio_bytes = 0

    async def _consume(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                event = _message(raw)
                kind = str(event.get("type", "unknown"))
                await self._emit(kind, event)
                item_id = str(event.get("item_id", "current"))
                if kind == "conversation.item.input_audio_transcription.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str) and delta:
                        partial = self._partials.get(item_id, "") + delta
                        self._partials[item_id] = partial
                        if self._on_partial is not None:
                            await self._on_partial(partial)
                elif kind == "conversation.item.input_audio_transcription.completed":
                    transcript = event.get("transcript")
                    final = transcript.strip() if isinstance(transcript, str) else ""
                    self._partials.pop(item_id, None)
                    if final and self._on_final is not None:
                        await self._on_final(final)
                elif kind == "error":
                    self._error = RuntimeError(self._error_detail(event))
                    return
        finally:
            self._ws = None

    async def _emit(self, kind: str, event: dict[str, Any]) -> None:
        if self._on_event is not None:
            await self._on_event(kind, cast(dict[str, object], event))

    @staticmethod
    def _error_detail(event: dict[str, Any]) -> str:
        error = event.get("error")
        if isinstance(error, dict):
            detail = cast(dict[str, object], error)
            message = detail.get("message")
            if isinstance(message, str):
                return f"OpenAI STT failed: {message[:180]}"
        return "OpenAI STT failed"
