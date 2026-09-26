from __future__ import annotations

import asyncio
import base64
import json
import logging
from contextlib import suppress
from typing import Any, cast

from websockets.asyncio.client import ClientConnection, connect

from sidecar.config import STTConfig, TTSConfig, secret
from sidecar.core.models import AudioResult
from sidecar.core.ports import EventCallback, TextCallback

logger = logging.getLogger("uvicorn.error").getChild("gradium")


def _json_message(raw: str | bytes) -> dict[str, Any]:
    decoded = raw.decode() if isinstance(raw, bytes) else raw
    value: object = json.loads(decoded)
    if not isinstance(value, dict):
        raise TypeError("Gradium message must be an object")
    return cast(dict[str, Any], value)


class GradiumSTT:
    def __init__(self, config: STTConfig) -> None:
        self.config = config
        self._secret = secret(config.secret_env)
        self._ws: ClientConnection | None = None
        self._consumer: asyncio.Task[None] | None = None
        self._on_partial: TextCallback | None = None
        self._on_final: TextCallback | None = None
        self._on_event: EventCallback | None = None
        self._fragments: list[str] = []
        self._finalizer: asyncio.Task[None] | None = None
        self._flush_counter = 0
        self._flush_pending = False
        self._quiet_steps = 0

    @property
    def available(self) -> bool:
        return self._secret is not None

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def start(
        self,
        on_partial: TextCallback,
        on_final: TextCallback,
        on_event: EventCallback,
        language: str,
    ) -> None:
        self._on_partial, self._on_final = on_partial, on_final
        self._on_event = on_event
        if self._secret is None or self._ws is not None:
            return
        self._ws = await connect(self.config.endpoint, additional_headers={"x-api-key": self._secret})
        await self._ws.send(
            json.dumps(
                {
                    "type": "setup",
                    "model_name": self.config.model,
                    "input_format": self.config.input_format,
                    "json_config": {
                        "language": language or self.config.language,
                        "delay_in_frames": self.config.delay_in_frames,
                    },
                }
            )
        )
        ready = _json_message(await self._ws.recv())
        if ready.get("type") != "ready":
            await self._ws.close()
            self._ws = None
            raise RuntimeError(
                f"Gradium STT did not become ready: {ready.get('code', ready.get('type'))}: "
                f"{str(ready.get('message', 'unknown error'))[:140]}"
            )
        await self._emit_event("ready", ready)
        logger.info(
            "stt.ready request=%s rate=%s frame=%s delay=%s",
            ready.get("request_id"),
            ready.get("sample_rate"),
            ready.get("frame_size"),
            ready.get("delay_in_frames"),
        )
        self._consumer = asyncio.create_task(self._consume())

    async def send(self, audio: bytes) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps({"type": "audio", "audio": base64.b64encode(audio).decode()}))

    async def flush(self) -> None:
        if self._ws is not None and self._fragments and not self._flush_pending:
            self._flush_counter += 1
            self._flush_pending = True
            await self._ws.send(json.dumps({"type": "flush", "flush_id": self._flush_counter}))

    async def stop(self) -> None:
        if self._ws is not None:
            with suppress(Exception):
                await self._ws.send(json.dumps({"type": "end_of_stream"}))
            await self._ws.close()
            self._ws = None
        if self._consumer is not None:
            self._consumer.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._consumer
            self._consumer = None
        if self._finalizer is not None:
            self._finalizer.cancel()
            with suppress(asyncio.CancelledError):
                await self._finalizer
            self._finalizer = None
        self._fragments.clear()
        self._flush_pending = False
        self._quiet_steps = 0

    async def _consume(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                message = _json_message(raw)
                if not await self._handle_message(message):
                    return
        finally:
            self._ws = None

    async def _handle_message(self, message: dict[str, Any]) -> bool:
        kind = message.get("type")
        await self._emit_event(str(kind), message)
        if kind == "text" and isinstance(message.get("text"), str):
            fragment = message["text"].strip()
            if fragment:
                self._fragments.append(fragment)
                logger.info("stt.fragment text=%r accumulated=%d", fragment, len(self._fragments))
                self._quiet_steps = 0
                if self._on_partial is not None:
                    await self._on_partial(" ".join(self._fragments))
        elif kind == "step":
            await self._handle_vad(message)
        elif kind == "flushed":
            logger.info("stt.flushed id=%s fragments=%d", message.get("flush_id"), len(self._fragments))
            self._flush_pending = False
            if self._finalizer is not None:
                self._finalizer.cancel()
            self._finalizer = asyncio.create_task(self._finalize_after_trailing_tokens())
        return kind != "error"

    async def _handle_vad(self, message: dict[str, Any]) -> None:
        vad = message.get("vad")
        if not self._fragments or not isinstance(vad, list) or not vad:
            return
        tail = vad[-1]
        probability = tail.get("inactivity_prob", 0) if isinstance(tail, dict) else 0
        self._quiet_steps = self._quiet_steps + 1 if float(probability) >= 0.9 else 0
        if self._quiet_steps >= 3:
            self._quiet_steps = 0
            await self.flush()

    async def _finalize_after_trailing_tokens(self) -> None:
        await asyncio.sleep(0.15)
        final = " ".join(self._fragments).strip()
        self._fragments.clear()
        if final and self._on_final is not None:
            logger.info("stt.final text=%r", final)
            await self._on_final(final)

    async def _emit_event(self, kind: str, message: dict[str, Any]) -> None:
        if self._on_event is None:
            return
        detail: dict[str, object] = {
            key: value
            for key, value in message.items()
            if key
            in {
                "request_id",
                "sample_rate",
                "frame_size",
                "delay_in_frames",
                "text",
                "flush_id",
                "code",
                "message",
            }
            and isinstance(value, str | int | float | bool)
        }
        vad = message.get("vad")
        if isinstance(vad, list) and vad and isinstance(vad[-1], dict):
            probability = vad[-1].get("inactivity_prob")
            if isinstance(probability, int | float):
                detail["inactivity_probability"] = float(probability)
        await self._on_event(kind, detail)


class GradiumTTS:
    def __init__(self, config: TTSConfig) -> None:
        self.config = config
        self._secret = secret(config.secret_env)
        self._voice_id = secret(config.voice_id_env) or config.voice_id

    @property
    def available(self) -> bool:
        return self._secret is not None and self._voice_id is not None

    async def synthesize(self, text: str, language: str) -> AudioResult:
        if self._secret is None or self._voice_id is None:
            raise RuntimeError("Gradium TTS requires API key and voice ID")
        chunks: list[bytes] = []
        sample_rate = 24000
        async with connect(self.config.endpoint, additional_headers={"x-api-key": self._secret}) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "setup",
                        "voice_id": self._voice_id,
                        "model_name": self.config.model,
                        "output_format": self.config.output_format,
                        "json_config": {
                            "temp": self.config.temperature,
                            "cfg_coef": self.config.cfg_coef,
                            "padding_bonus": self.config.padding_bonus,
                            "rewrite_rules": language,
                        },
                    }
                )
            )
            ready = _json_message(await ws.recv())
            if ready.get("type") != "ready":
                raise RuntimeError(f"Gradium TTS did not become ready: {ready.get('type')}")
            if isinstance(ready.get("sample_rate"), int):
                sample_rate = ready["sample_rate"]
            await ws.send(json.dumps({"type": "text", "text": text}))
            await ws.send(json.dumps({"type": "end_of_stream"}))
            async for raw in ws:
                message = _json_message(raw)
                if message.get("type") == "audio" and isinstance(message.get("audio"), str):
                    chunks.append(base64.b64decode(message["audio"]))
                elif message.get("type") == "end_of_stream":
                    break
                elif message.get("type") == "error":
                    raise RuntimeError(str(message.get("message", "Gradium TTS failed")))
        return AudioResult(
            data_base64=base64.b64encode(b"".join(chunks)).decode(),
            format=self.config.output_format,
            sample_rate=sample_rate,
        )
