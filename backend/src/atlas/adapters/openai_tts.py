from __future__ import annotations

import base64
from collections.abc import AsyncIterator

import httpx

from atlas.config import TTSModelConfig, secret
from atlas.core.models import AudioResult
from atlas.core.ports import AudioChunkCallback, TextTimingCallback


class OpenAITTS:
    def __init__(self, config: TTSModelConfig) -> None:
        self.config = config
        self._secret = secret(config.secret_env)

    @property
    def available(self) -> bool:
        return self._secret is not None

    async def synthesize(
        self,
        text: str,
        language: str,
        padding_bonus: float | None = None,
        temp: float | None = None,
    ) -> AudioResult:
        del padding_bonus, temp
        chunks: list[bytes] = []

        async def collect(chunk: bytes, _sample_rate: int, _format: str) -> None:
            chunks.append(chunk)

        await self.stream(text, language, collect)
        return AudioResult(
            data_base64=base64.b64encode(b"".join(chunks)).decode(),
            format="pcm_24000",
            sample_rate=24000,
        )

    async def stream(
        self,
        text: str,
        language: str,
        on_chunk: AudioChunkCallback,
        on_text: TextTimingCallback | None = None,
    ) -> None:
        if self._secret is None:
            raise RuntimeError("OpenAI TTS requires an API key")
        instructions = f"{self.config.instructions} Reply language: {language}."
        subtitle_sent = False
        async with (
            httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10)) as client,
            client.stream(
                "POST",
                self.config.endpoint,
                headers={"Authorization": f"Bearer {self._secret}"},
                json={
                    "model": self.config.model,
                    "voice": self.config.voice_id,
                    "input": text,
                    "instructions": instructions,
                    "response_format": "pcm",
                    "stream_format": "audio",
                },
            ) as response,
        ):
            if response.is_error:
                raise RuntimeError(f"OpenAI TTS failed with HTTP {response.status_code}")
            async for chunk in response.aiter_bytes():
                if chunk:
                    if on_text is not None and not subtitle_sent:
                        await on_text(text, 0.0, 0.0)
                        subtitle_sent = True
                    await on_chunk(chunk, 24000, "pcm_24000")

    async def stream_chunks(
        self,
        text_chunks: AsyncIterator[str],
        language: str,
        on_chunk: AudioChunkCallback,
        on_text: TextTimingCallback | None = None,
    ) -> None:
        segments: list[str] = []
        async for text in text_chunks:
            cleaned = text.strip()
            if cleaned:
                segments.append(cleaned)
        if segments:
            await self.stream(" ".join(segments), language, on_chunk, on_text)
