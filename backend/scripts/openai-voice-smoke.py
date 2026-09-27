from __future__ import annotations

import asyncio
import base64

from atlas.adapters.openai_stt import OpenAISTT
from atlas.adapters.openai_tts import OpenAITTS
from atlas.config import load_config


async def main() -> None:
    config = load_config()
    stt_config = next(item for item in config.voice.stt.providers if item.id == config.voice.stt.active)
    tts_config = next(item for item in config.voice.tts.providers if item.id == config.voice.tts.active)
    if stt_config.provider != "openai" or tts_config.provider != "openai":
        raise RuntimeError("OpenAI voice smoke requires OpenAI to be active for STT and TTS")

    expected = "Atlas, we are testing the OpenAI voice fallback."
    result = await OpenAITTS(tts_config).synthesize(expected, "en")
    pcm = base64.b64decode(result.data_base64)
    final: asyncio.Queue[str] = asyncio.Queue()
    stt = OpenAISTT(stt_config)

    async def partial(_: str) -> None:
        return None

    async def completed(text: str) -> None:
        await final.put(text)

    async def event(_: str, __: dict[str, object]) -> None:
        return None

    await stt.start(partial, completed, event, "en")
    try:
        for offset in range(0, len(pcm), 3840):
            await stt.send(pcm[offset : offset + 3840])
            await asyncio.sleep(0.08)
        for _ in range(12):
            await stt.send(bytes(3840))
            await asyncio.sleep(0.08)
        await stt.flush()
        transcript = await asyncio.wait_for(final.get(), timeout=20)
        print(f"openai_tts_bytes={len(pcm)}")
        print(f"openai_stt_transcript={transcript!r}")
    finally:
        await stt.stop()


if __name__ == "__main__":
    asyncio.run(main())
