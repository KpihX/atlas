from __future__ import annotations

import asyncio
import json
import os
import sys

from websockets.asyncio.client import connect

from atlas.config import load_product


async def main(session_id: str) -> None:
    url = os.environ.get("ATLAS_WS_URL", "ws://127.0.0.1:8787/v1/live")
    async with connect(url) as websocket:
        await websocket.send(
            json.dumps(
                {
                    "type": "client.hello",
                    "protocol_version": load_product().protocol_version,
                    "client_id": "curate-session",
                    "capabilities": {"audio_capture": False, "audio_playback": False},
                }
            )
        )
        await websocket.send(json.dumps({"type": "session.open", "session_id": session_id}))
        before: int | None = None
        previous_results: int | None = None
        requested = False
        async with asyncio.timeout(120):
            async for raw in websocket:
                message = json.loads(raw)
                if message.get("type") != "state.snapshot":
                    continue
                state = message["state"]
                if state.get("session_id") != session_id:
                    continue
                before = before if before is not None else len(state.get("cards", []))
                activities = state.get("activities", [])
                results = [item for item in activities if item.get("kind") in {"board.curated", "error"}]
                if previous_results is None:
                    previous_results = len(results)
                    await websocket.send(json.dumps({"type": "board.curate"}))
                    requested = True
                    continue
                if requested and len(results) > previous_results:
                    result = results[-1]
                    print(
                        json.dumps(
                            {
                                "session_id": session_id,
                                "before": before,
                                "after": len(state.get("cards", [])),
                                "result": result.get("summary"),
                            }
                        )
                    )
                    return


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: curate-session.py <session_id>")
    asyncio.run(main(sys.argv[1]))
