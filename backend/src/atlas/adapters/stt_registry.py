from __future__ import annotations

from collections.abc import Mapping

from atlas.config import STTRegistryConfig
from atlas.core.ports import EventCallback, STTPort, TextCallback


class STTRegistry:
    def __init__(self, config: STTRegistryConfig, providers: Mapping[str, STTPort]) -> None:
        self.config = config
        self.providers = providers
        self._active: STTPort | None = None
        self._callbacks: tuple[TextCallback, TextCallback, EventCallback, str] | None = None

    @property
    def available(self) -> bool:
        return any(provider.available for provider in self.providers.values())

    @property
    def connected(self) -> bool:
        return self._active is not None and self._active.connected

    @property
    def rotate_after_seconds(self) -> float:
        if self._active is not None:
            return self._active.rotate_after_seconds
        return self.config.ordered()[0].rotate_after_seconds

    async def start(
        self,
        on_partial: TextCallback,
        on_final: TextCallback,
        on_event: EventCallback,
        language: str,
    ) -> None:
        self._callbacks = (on_partial, on_final, on_event, language)
        failures: list[str] = []
        for provider_config in self.config.ordered():
            provider = self.providers[provider_config.id]
            if not provider.available:
                failures.append(f"{provider_config.id}: missing credentials")
                continue
            try:
                await provider.start(on_partial, on_final, on_event, language)
                if provider.connected:
                    self._active = provider
                    await on_event("provider.selected", {"provider": provider_config.id})
                    return
            except Exception as error:
                failures.append(f"{provider_config.id}: {type(error).__name__}: {str(error)[:120]}")
                await provider.stop()
        raise RuntimeError("No STT provider available; " + "; ".join(failures))

    async def send(self, audio: bytes) -> None:
        if self._active is None:
            return
        try:
            await self._active.send(audio)
        except Exception as error:
            failed = self._active
            await failed.stop()
            self._active = None
            if self._callbacks is None:
                raise
            await self.start(*self._callbacks)
            await self._selected_after_failover(error).send(audio)

    async def flush(self) -> None:
        if self._active is not None:
            await self._active.flush()

    async def stop(self) -> None:
        if self._active is not None:
            await self._active.stop()
            self._active = None

    def _selected_after_failover(self, cause: Exception) -> STTPort:
        if self._active is None:
            raise RuntimeError("STT failover did not select a provider") from cause
        return self._active
