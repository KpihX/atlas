from __future__ import annotations

import asyncio
import json
from pathlib import Path
from time import monotonic
from typing import Any, cast

import httpx
import websockets


def working_key() -> str:
    for line in Path("../.env").read_text().splitlines():
        if line.startswith("OPENAI_API_KEY="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise RuntimeError("working OPENAI_API_KEY not found")


TEXT = (
    "Je suis votre collaborateur de réunion. "
    "Je peux écouter, structurer les idées et coordonner des recherches."
)


async def tts_trial(client: httpx.AsyncClient, api_key: str, trial: int) -> None:
    started = monotonic()
    first_audio_ms: int | None = None
    byte_count = 0
    async with client.stream(
        "POST",
        "https://api.openai.com/v1/audio/speech",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": "gpt-4o-mini-tts",
            "voice": "marin",
            "input": TEXT,
            "instructions": "Parle naturellement en français, chaleureusement et sans lire la ponctuation.",
            "response_format": "pcm",
            "stream_format": "audio",
        },
    ) as response:
        if response.is_error:
            print(f"tts trial={trial} status={response.status_code}")
            return
        async for chunk in response.aiter_bytes():
            if chunk and first_audio_ms is None:
                first_audio_ms = round((monotonic() - started) * 1000)
            byte_count += len(chunk)
    print(
        f"tts trial={trial} first_audio_ms={first_audio_ms} "
        f"total_ms={round((monotonic() - started) * 1000)} bytes={byte_count}"
    )


async def realtime_trial(api_key: str, trial: int) -> None:
    started = monotonic()
    first_audio_ms: int | None = None
    byte_count = 0
    transcript = ""
    async with websockets.connect(
        "wss://api.openai.com/v1/realtime?model=gpt-realtime-2.1",
        additional_headers={"Authorization": f"Bearer {api_key}"},
        open_timeout=20,
        close_timeout=5,
    ) as socket:
        await socket.send(
            json.dumps(
                {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "model": "gpt-realtime-2.1",
                        "output_modalities": ["audio"],
                        "reasoning": {"effort": "low"},
                        "audio": {
                            "output": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "voice": "marin",
                            }
                        },
                        "instructions": (
                            "Tu es le collaborateur vocal unifié d'une réunion. "
                            "Réponds naturellement en français, sans mentionner de modèle interne."
                        ),
                    },
                }
            )
        )
        await socket.send(
            json.dumps(
                {
                    "type": "conversation.item.create",
                    "item": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": TEXT}],
                    },
                }
            )
        )
        await socket.send(json.dumps({"type": "response.create"}))
        async with asyncio.timeout(30):
            async for raw in socket:
                value: object = json.loads(raw)
                if not isinstance(value, dict):
                    continue
                event = cast(dict[str, Any], value)
                event_type = event.get("type")
                if event_type == "response.output_audio.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str):
                        if first_audio_ms is None:
                            first_audio_ms = round((monotonic() - started) * 1000)
                        byte_count += len(delta) * 3 // 4
                elif event_type == "response.output_audio_transcript.delta":
                    delta = event.get("delta")
                    if isinstance(delta, str):
                        transcript += delta
                elif event_type == "error":
                    error = event.get("error")
                    print(f"realtime trial={trial} error={error}")
                    return
                elif event_type == "response.done":
                    break
    print(
        f"realtime trial={trial} first_audio_ms={first_audio_ms} "
        f"total_ms={round((monotonic() - started) * 1000)} "
        f"bytes={byte_count} transcript_chars={len(transcript)}"
    )


async def main() -> None:
    api_key = working_key()
    for trial in range(1, 4):
        await realtime_trial(api_key, trial)


if __name__ == "__main__":
    asyncio.run(main())
