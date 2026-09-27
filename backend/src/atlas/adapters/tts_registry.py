from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from atlas.config import TTSRegistryConfig
from atlas.core.models import AudioResult
from atlas.core.ports import AudioChunkCallback, TextTimingCallback, TTSPort


class TTSRegistry:
    def __init__(self, config: TTSRegistryConfig, providers: Mapping[str, TTSPort]) -> None:
        self.config = config
        self.providers = providers

    @property
    def available(self) -> bool:
        return any(provider.available for provider in self.providers.values())

    async def synthesize(
        self,
        text: str,
        language: str,
        padding_bonus: float | None = None,
        temp: float | None = None,
    ) -> AudioResult:
        failures: list[str] = []
        for provider_config in self.config.ordered():
            provider = self.providers[provider_config.id]
            if not provider.available:
                failures.append(f"{provider_config.id}: missing credentials")
                continue
            try:
                return await provider.synthesize(text, language, padding_bonus, temp)
            except Exception as error:
                failures.append(f"{provider_config.id}: {type(error).__name__}: {str(error)[:120]}")
        raise RuntimeError("No TTS provider available; " + "; ".join(failures))

    async def stream(
        self,
        text: str,
        language: str,
        on_chunk: AudioChunkCallback,
        on_text: TextTimingCallback | None = None,
    ) -> None:
        async def chunks() -> AsyncIterator[str]:
            yield text

        await self.stream_chunks(chunks(), language, on_chunk, on_text)

    async def stream_chunks(
        self,
        text_chunks: AsyncIterator[str],
        language: str,
        on_chunk: AudioChunkCallback,
        on_text: TextTimingCallback | None = None,
    ) -> None:
        consumed: list[str] = []
        source = text_chunks
        failures: list[str] = []
        for provider_config in self.config.ordered():
            provider = self.providers[provider_config.id]
            if not provider.available:
                failures.append(f"{provider_config.id}: missing credentials")
                continue
            emitted = False

            async def tracked_audio(data: bytes, sample_rate: int, audio_format: str) -> None:
                nonlocal emitted
                emitted = True
                await on_chunk(data, sample_rate, audio_format)

            async def replayable() -> AsyncIterator[str]:
                if consumed:
                    for text in consumed:
                        yield text
                    consumed.clear()
                async for text in source:
                    consumed.append(text)
                    yield text

            try:
                await provider.stream_chunks(replayable(), language, tracked_audio, on_text)
                return
            except Exception as error:
                if emitted:
                    raise
                failures.append(f"{provider_config.id}: {type(error).__name__}: {str(error)[:120]}")
        raise RuntimeError("No TTS provider available; " + "; ".join(failures))
