from __future__ import annotations

import asyncio
import json
import os

from websockets.asyncio.client import connect


async def main() -> None:
    url = os.environ.get("SIDECAR_WS_URL", "ws://127.0.0.1:8787/v1/live")
    async with connect(url) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "client.hello",
                    "protocol_version": 7,
                    "client_id": "live-smoke",
                    "capabilities": {"audio_capture": False, "audio_playback": False},
                }
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "type": "session.start",
                    "assistant_name": "Assistant",
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
                        "Assistant, cherche avec Exa la documentation officielle la plus recente de Python "
                        "et donne une reponse tres courte avec la source."
                    ),
                }
            )
        )
        async with asyncio.timeout(60):
            async for raw in websocket:
                message = json.loads(raw)
                kind = message.get("type")
                if kind == "speech.authorized":
                    print(
                        json.dumps(
                            {
                                "type": kind,
                                "text": message.get("text"),
                                "reason": message.get("reason"),
                                "audio": bool(message.get("audio")),
                            }
                        )
                    )
                    return
                if kind == "state.snapshot":
                    state = message["state"]
                    activities = state.get("activities", [])
                    if activities and activities[-1].get("kind") == "error":
                        print(json.dumps({"type": kind, "error": activities[-1].get("summary")}))
                        return


if __name__ == "__main__":
    asyncio.run(main())
