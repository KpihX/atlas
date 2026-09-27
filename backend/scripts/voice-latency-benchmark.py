from __future__ import annotations

import asyncio
from time import monotonic

from atlas.adapters.gradium import GradiumTTS
from atlas.config import load_config


async def main() -> None:
    config = load_config()
    gradium = next(item for item in config.voice.tts.providers if item.provider == "gradium")
    tts = GradiumTTS(gradium)
    started = monotonic()
    first_chunk_ms: int | None = None
    chunk_count = 0
    byte_count = 0

    async def receive(data: bytes, sample_rate: int, audio_format: str) -> None:
        nonlocal first_chunk_ms, chunk_count, byte_count
        if first_chunk_ms is None:
            first_chunk_ms = round((monotonic() - started) * 1000)
        chunk_count += 1
        byte_count += len(data)
        if chunk_count == 1:
            print(f"first_chunk_ms={first_chunk_ms} sample_rate={sample_rate} format={audio_format}")

    await tts.stream(
        "Je peux écouter la réunion, maintenir les notes et lancer des recherches utiles.",
        "fr",
        receive,
    )
    total_ms = round((monotonic() - started) * 1000)
    print(f"total_ms={total_ms} chunks={chunk_count} bytes={byte_count}")


if __name__ == "__main__":
    asyncio.run(main())
