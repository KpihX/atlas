from __future__ import annotations

import asyncio
import json
import os

import httpx
from websockets.asyncio.client import ClientConnection, connect


async def wait_for_speech(websocket: ClientConnection) -> dict[str, object]:
    async with asyncio.timeout(90):
        async for raw in websocket:
            message: dict[str, object] = json.loads(raw)
            if message.get("type") == "speech.authorized":
                return message
    raise RuntimeError("No speech authorized")


async def main() -> None:
    base = os.environ.get("SIDECAR_HTTP_URL", "http://127.0.0.1:8787")
    session_id = ""
    async with connect(base.replace("http", "ws", 1) + "/v1/live") as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "client.hello",
                    "protocol_version": 7,
                    "client_id": "conversation-smoke",
                    "capabilities": {"audio_capture": False, "audio_playback": False},
                }
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "type": "session.start",
                    "assistant_name": "Assistant",
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
                    "text": (
                        "Assistant, without web research, explain in three sentences why agentic systems "
                        "could help a medical hackathon."
                    ),
                }
            )
        )
        first = await wait_for_speech(websocket)
        await websocket.send(
            json.dumps(
                {
                    "type": "playback.finished",
                    "speech_id": first["speech_id"],
                }
            )
        )
        await websocket.send(
            json.dumps(
                {
                    "type": "transcript.inject",
                    "text": "Assistant, repeat that and clarify the second point.",
                }
            )
        )
        second = await wait_for_speech(websocket)
        await websocket.send(json.dumps({"type": "session.command", "command": "stop"}))
        async with asyncio.timeout(15):
            async for raw in websocket:
                message = json.loads(raw)
                if message.get("type") == "state.snapshot":
                    session_id = str(message["state"].get("session_id") or "")
                    if message["state"].get("session_status") == "closed":
                        if message["state"].get("tasks"):
                            raise RuntimeError("Direct contextual answer unexpectedly used a tool")
                        break
        print(json.dumps({"first": first.get("text"), "second": second.get("text")}))
    if session_id:
        async with httpx.AsyncClient() as client:
            response = await client.delete(f"{base}/v1/sessions/{session_id}")
            response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())
