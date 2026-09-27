from __future__ import annotations

import asyncio
import json
import os

import httpx
from websockets.asyncio.client import ClientConnection, connect

from atlas.config import load_product


async def wait_for_speech(websocket: ClientConnection) -> dict[str, object]:
    speech_id = ""
    subtitles: dict[int, str] = {}
    async with asyncio.timeout(90):
        async for raw in websocket:
            message: dict[str, object] = json.loads(raw)
            if message.get("type") == "speech.authorized":
                speech_id = str(message.get("speech_id", ""))
            if (
                message.get("type") == "speech.subtitle"
                and not bool(message.get("final"))
                and str(message.get("speech_id", "")) == speech_id
                and str(message.get("text", "")).strip()
            ):
                raw_segment_index = message.get("segment_index", 0)
                segment_index = raw_segment_index if isinstance(raw_segment_index, int) else 0
                subtitles[segment_index] = str(message.get("text", "")).strip()
            if (
                message.get("type") == "speech.subtitle"
                and bool(message.get("final"))
                and str(message.get("speech_id", "")) == speech_id
            ):
                return {
                    "speech_id": speech_id,
                    "text": " ".join(subtitles[index] for index in sorted(subtitles)),
                }
    raise RuntimeError("No speech authorized")


async def main() -> None:
    base = os.environ.get("ATLAS_HTTP_URL", "http://127.0.0.1:8787")
    session_id = ""
    async with connect(base.replace("http", "ws", 1) + "/v1/live") as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "client.hello",
                    "protocol_version": load_product().protocol_version,
                    "client_id": "conversation-smoke",
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
            json.dumps(
                {
                    "type": "transcript.inject",
                    "text": (
                        "Atlas, without web research, explain in three sentences why agentic systems "
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
                    "text": "Atlas, repeat that and clarify the second point.",
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
