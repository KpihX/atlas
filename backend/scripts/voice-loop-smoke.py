from __future__ import annotations

import asyncio
import base64

from sidecar.adapters.gradium import GradiumSTT, GradiumTTS
from sidecar.config import load_config


async def transcribe(sentences: list[str], language: str) -> list[str]:
    config = load_config()
    partial = ""
    finals: asyncio.Queue[str] = asyncio.Queue()

    async def on_partial(text: str) -> None:
        nonlocal partial
        partial = text

    async def on_final(text: str) -> None:
        await finals.put(text)

    async def on_event(_: str, __: dict[str, object]) -> None:
        pass

    stt = GradiumSTT(config.voice.stt)
    await stt.start(on_partial, on_final, on_event, language)
    results: list[str] = []
    for sentence in sentences:
        pcm_tts = config.voice.tts.model_copy(update={"output_format": "pcm_24000"})
        audio = await GradiumTTS(pcm_tts).synthesize(sentence, language)
        pcm = base64.b64decode(audio.data_base64)
        for offset in range(0, len(pcm), 3840):
            await stt.send(pcm[offset : offset + 3840])
            await asyncio.sleep(0.08)
        await stt.flush()
        final = await asyncio.wait_for(finals.get(), timeout=15)
        results.append(final)
        print(f"{language}.expected: {sentence}")
        print(f"{language}.partial:  {partial}")
        print(f"{language}.final:    {final}")
    await stt.stop()
    return results


async def main() -> None:
    await transcribe(
        [
            "Assistant, we are starting a new session to prepare the project.",
            "The second sentence must continue on the same connection.",
            "The third sentence verifies that transcription does not freeze.",
        ],
        "en",
    )
    await transcribe(["Assistant, nous lançons une nouvelle session pour préparer le projet."], "fr")


if __name__ == "__main__":
    asyncio.run(main())
