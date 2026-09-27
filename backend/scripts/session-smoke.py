from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from websockets.asyncio.client import ClientConnection, connect

from atlas.config import load_product


async def snapshot(websocket: ClientConnection, status: str) -> dict[str, Any]:
    async with asyncio.timeout(15):
        async for raw in websocket:
            message: dict[str, Any] = json.loads(raw)
            if message.get("type") == "state.snapshot" and message["state"]["session_status"] == status:
                return message
    raise RuntimeError(f"No {status} snapshot")


async def main() -> None:
    url = os.environ.get("ATLAS_WS_URL", "ws://127.0.0.1:8787/v1/live")
    async with connect(url) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "client.hello",
                    "protocol_version": load_product().protocol_version,
                    "client_id": "session-smoke",
                    "capabilities": {"audio_capture": False, "audio_playback": False},
                }
            )
        )
        start = {
            "type": "session.start",
            "language": "fr",
            "capture_mode": "microphone",
            "output_mode": "local_only",
        }
        await websocket.send(json.dumps(start))
        first = await snapshot(websocket, "listening")
        first_id = first["state"]["session_id"]
        await websocket.send(json.dumps({"type": "session.command", "command": "stop"}))
        await snapshot(websocket, "closed")

        await websocket.send(json.dumps(start))
        second = await snapshot(websocket, "listening")
        second_id = second["state"]["session_id"]
        await websocket.send(json.dumps({"type": "session.open", "session_id": first_id}))
        resumed = await snapshot(websocket, "paused")
        await websocket.send(json.dumps({"type": "session.command", "command": "resume"}))
        resumed = await snapshot(websocket, "listening")
        await websocket.send(json.dumps({"type": "session.command", "command": "stop"}))
        await snapshot(websocket, "closed")
        print(
            json.dumps(
                {
                    "first": first_id,
                    "second": second_id,
                    "resumed": resumed["state"]["session_id"],
                    "sessions": len(resumed["sessions"]),
                }
            )
        )


if __name__ == "__main__":
    asyncio.run(main())
