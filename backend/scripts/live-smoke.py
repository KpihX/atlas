from __future__ import annotations

import asyncio
import json
import os
from time import monotonic

import httpx
from websockets.asyncio.client import connect

from atlas.config import load_product


async def main() -> None:
    url = os.environ.get("ATLAS_WS_URL", "ws://127.0.0.1:8787/v1/live")
    session_id = ""
    try:
        async with connect(url) as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "type": "client.hello",
                        "protocol_version": load_product().protocol_version,
                        "client_id": "live-smoke",
                        "capabilities": {"audio_capture": False, "audio_playback": False},
                    }
                )
            )
            await websocket.send(
                json.dumps(
                    {
                        "type": "session.start",
                        "language": "fr",
                        "capture_mode": "microphone",
                        "output_mode": "local_only",
                    }
                )
            )
            await websocket.send(
                json.dumps(
                    {
                        "type": "transcript.inject",
                        "text": (
                            "Atlas, présente-toi en une phrase et dis ce que tu peux faire "
                            "dans cette session."
                        ),
                    }
                )
            )
            started = monotonic()
            authorized_text = ""
            async with asyncio.timeout(30):
                async for raw in websocket:
                    message = json.loads(raw)
                    kind = message.get("type")
                    if kind == "speech.authorized":
                        authorized_text = str(message.get("text", ""))
                    if kind == "speech.audio.chunk":
                        print(
                            json.dumps(
                                {
                                    "type": kind,
                                    "text": authorized_text,
                                    "first_audio_ms": round((monotonic() - started) * 1000),
                                    "sequence": message.get("sequence"),
                                },
                                ensure_ascii=False,
                            )
                        )
                        return
                    if kind == "state.snapshot":
                        state = message["state"]
                        session_id = str(state.get("session_id") or session_id)
                        activities = state.get("activities", [])
                        if activities and activities[-1].get("kind") == "error":
                            print(json.dumps({"type": kind, "error": activities[-1].get("summary")}))
                            return
    finally:
        if session_id:
            async with httpx.AsyncClient() as client:
                await client.delete(f"http://127.0.0.1:8787/v1/sessions/{session_id}")


if __name__ == "__main__":
    asyncio.run(main())
