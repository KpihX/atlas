from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from .models import AtlasState, AudioResult, Decision, LLMResult, SessionSummary

Publish = Callable[[dict[str, object]], Awaitable[None]]
TextCallback = Callable[[str], Awaitable[None]]
EventCallback = Callable[[str, dict[str, object]], Awaitable[None]]
AudioChunkCallback = Callable[[bytes, int, str], Awaitable[None]]
TextTimingCallback = Callable[[str, float, float], Awaitable[None]]


class DecisionPort(Protocol):
    async def evaluate(self, state: AtlasState, text: str) -> Decision: ...


class GeneratorPort(Protocol):
    @property
    def available(self) -> bool: ...

    async def generate(self, messages: list[dict[str, str]], model_id: str | None = None) -> LLMResult: ...

    def stream(self, messages: list[dict[str, str]], model_id: str | None = None) -> AsyncIterator[str]: ...


class STTPort(Protocol):
    @property
    def available(self) -> bool: ...

    @property
    def connected(self) -> bool: ...

    async def start(
        self,
        on_partial: TextCallback,
        on_final: TextCallback,
        on_event: EventCallback,
        language: str,
    ) -> None: ...

    async def send(self, audio: bytes) -> None: ...

    async def flush(self) -> None: ...

    async def stop(self) -> None: ...


class TTSPort(Protocol):
    @property
    def available(self) -> bool: ...

    async def synthesize(
        self,
        text: str,
        language: str,
        padding_bonus: float | None = None,
        temp: float | None = None,
    ) -> AudioResult: ...

    async def stream(
        self,
        text: str,
        language: str,
        on_chunk: AudioChunkCallback,
        on_text: TextTimingCallback | None = None,
    ) -> None: ...

    async def stream_chunks(
        self,
        text_chunks: AsyncIterator[str],
        language: str,
        on_chunk: AudioChunkCallback,
        on_text: TextTimingCallback | None = None,
    ) -> None: ...


class StorePort(Protocol):
    async def open(self) -> None: ...

    async def load(self, session_id: str) -> AtlasState | None: ...

    async def list_sessions(self) -> list[SessionSummary]: ...

    async def save(self, state: AtlasState) -> None: ...

    async def delete(self, session_id: str) -> bool: ...

    async def close(self) -> None: ...
