from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import httpx
from websockets.asyncio.client import ClientConnection, connect

from atlas.config import load_product


async def wait_state(websocket: ClientConnection, key: str, value: str) -> dict[str, object]:
    async with asyncio.timeout(90):
        async for raw in websocket:
            message: dict[str, object] = json.loads(raw)
            if message.get("type") == "state.snapshot":
                state_value = message.get("state")
                if not isinstance(state_value, dict):
                    continue
                state = cast(dict[str, Any], state_value)
                if state.get(key) == value:
                    return cast(dict[str, object], state)
    raise RuntimeError(f"No state with {key}={value}")


async def main() -> None:
    async with connect("ws://127.0.0.1:8787/v1/live") as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "client.hello",
                    "protocol_version": load_product().protocol_version,
                    "client_id": "control-smoke",
                    "capabilities": {"audio_capture": False, "audio_playback": False},
                }
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "type": "session.start",
                    "language": "en",
                    "capture_mode": "microphone",
                    "output_mode": "local_only",
                }
            )
        )
        await websocket.send(
            json.dumps({"type": "transcript.inject", "text": "Atlas, stay silent but keep listening."})
        )
        muted = await wait_state(websocket, "voice_mode", "muted")
        await websocket.send(
            json.dumps({"type": "transcript.inject", "text": "Atlas, you may speak again now."})
        )
        await wait_state(websocket, "voice_mode", "active")
        await websocket.send(
            json.dumps({"type": "transcript.inject", "text": "Atlas, end this session now."})
        )
        closed = await wait_state(websocket, "session_status", "closed")
        session_id = str(closed["session_id"])
        print(json.dumps({"muted": muted["voice_mode"], "closed": closed["session_status"]}))
    async with httpx.AsyncClient() as client:
        response = await client.delete(f"http://127.0.0.1:8787/v1/sessions/{session_id}")
        response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())
