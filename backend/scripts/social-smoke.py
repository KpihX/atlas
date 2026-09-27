from __future__ import annotations

import asyncio
import json

import httpx
from websockets.asyncio.client import ClientConnection, connect

from atlas.config import load_product


async def wait_type(websocket: ClientConnection, kind: str, timeout_seconds: float = 90) -> dict[str, object]:
    async with asyncio.timeout(timeout_seconds):
        async for raw in websocket:
            message: dict[str, object] = json.loads(raw)
            if message.get("type") == kind:
                return message
    raise RuntimeError(f"No {kind}")


async def main() -> None:
    session_id = ""
    async with connect("ws://127.0.0.1:8787/v1/live") as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "client.hello",
                    "protocol_version": load_product().protocol_version,
                    "client_id": "social-smoke",
                    "capabilities": {"audio_capture": False, "audio_playback": True},
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
            json.dumps(
                {
                    "type": "transcript.inject",
                    "text": "Atlas, explain the project naturally in enough detail to speak for a while.",
                }
            )
        )
        speech = await wait_type(websocket, "speech.authorized")
        speech_id = str(speech["speech_id"])
        await websocket.send(json.dumps({"type": "playback.started", "speech_id": speech_id}))
        await websocket.send(json.dumps({"type": "floor.changed", "busy": True}))
        await wait_type(websocket, "speech.stop", timeout_seconds=10)
        await websocket.send(json.dumps({"type": "floor.changed", "busy": False}))
        await websocket.send(
            json.dumps(
                {
                    "type": "transcript.inject",
                    "text": "Atlas, prepare another detailed spoken answer.",
                }
            )
        )
        await websocket.send(json.dumps({"type": "session.command", "command": "stop"}))
        closed = False
        async with asyncio.timeout(12):
            async for raw in websocket:
                message = json.loads(raw)
                if message.get("type") == "speech.authorized":
                    raise RuntimeError("Speech escaped after End")
                if message.get("type") == "state.snapshot":
                    session_id = str(message["state"].get("session_id") or "")
                    if message["state"].get("session_status") == "closed":
                        closed = True
                        break
        if not closed:
            raise RuntimeError("Session did not close")
        print(json.dumps({"barge_in": "interrupted", "end": "silent"}))
    if session_id:
        async with httpx.AsyncClient() as client:
            response = await client.delete(f"http://127.0.0.1:8787/v1/sessions/{session_id}")
            response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())
