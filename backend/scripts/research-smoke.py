from __future__ import annotations

import asyncio
import json

import httpx
from websockets.asyncio.client import connect

from atlas.config import load_product


async def main() -> None:
    base = "http://127.0.0.1:8787"
    session_id = ""
    acknowledged = False
    task_completed = False
    result_reported = False
    notes_updated = False
    finding_integrated = False
    board_updated = False
    observed_phases: set[str] = set()
    speech_reasons: dict[str, str] = {}
    last_state: dict[str, object] = {}
    try:
        async with connect("ws://127.0.0.1:8787/v1/live") as websocket:
            await websocket.send(
                json.dumps(
                    {
                        "type": "client.hello",
                        "protocol_version": load_product().protocol_version,
                        "client_id": "research-smoke",
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
                            "Atlas, recherche avec Exa la documentation officielle sur les alertes "
                            "d'inondation au Népal et donne la source principale."
                        ),
                    }
                )
            )

            try:
                async with asyncio.timeout(90):
                    async for raw in websocket:
                        message = json.loads(raw)
                        if message.get("type") == "speech.authorized":
                            speech_id = str(message.get("speech_id", ""))
                            reason = str(message.get("reason", ""))
                            speech_reasons[speech_id] = reason
                            if reason == "direct_address":
                                acknowledged = True
                        if message.get("type") == "speech.audio.end":
                            speech_id = str(message.get("speech_id", ""))
                            await websocket.send(
                                json.dumps({"type": "playback.finished", "speech_id": speech_id})
                            )
                            if speech_reasons.get(speech_id) == "requested_result":
                                result_reported = True
                        if message.get("type") != "state.snapshot":
                            continue
                        state = message["state"]
                        last_state = state
                        session_id = str(state.get("session_id") or session_id)
                        notes_updated = notes_updated or int(state.get("notes_version", 0)) > 0
                        notes_document = state.get("notes_document", {})
                        finding_integrated = finding_integrated or bool(notes_document.get("findings", []))
                        board_updated = board_updated or bool(state.get("cards", []))
                        for task in state.get("tasks", []):
                            observed_phases.add(str(task.get("phase", "")))
                        task_completed = task_completed or any(
                            task.get("status") == "done"
                            and task.get("phase") == "complete"
                            and task.get("tool") == "exa_search"
                            for task in state.get("tasks", [])
                        )
                        no_running_task = not any(
                            task.get("status") in {"queued", "running"} for task in state.get("tasks", [])
                        )
                        if all(
                            (
                                acknowledged,
                                task_completed,
                                result_reported,
                                notes_updated,
                                finding_integrated,
                                board_updated,
                                no_running_task,
                            )
                        ):
                            if "planning" not in observed_phases and "executing" not in observed_phases:
                                raise RuntimeError(
                                    f"Worker phases were not observable: {sorted(observed_phases)}"
                                )
                            print(
                                json.dumps(
                                    {
                                        "acknowledged": acknowledged,
                                        "exa_completed": task_completed,
                                        "result_reported": result_reported,
                                        "notes_updated": notes_updated,
                                        "finding_integrated": finding_integrated,
                                        "board_updated": board_updated,
                                        "phases": sorted(observed_phases),
                                    }
                                )
                            )
                            return
            except TimeoutError:
                print(
                    json.dumps(
                        {
                            "timeout": True,
                            "acknowledged": acknowledged,
                            "exa_completed": task_completed,
                            "result_reported": result_reported,
                            "notes_updated": notes_updated,
                            "finding_integrated": finding_integrated,
                            "board_updated": board_updated,
                            "phases": sorted(observed_phases),
                            "tasks": last_state.get("tasks", []),
                            "speeches": last_state.get("speeches", []),
                            "agent_runs": last_state.get("agent_runs", []),
                        },
                        ensure_ascii=False,
                    )
                )
                raise
    finally:
        if session_id:
            async with httpx.AsyncClient() as client:
                response = await client.delete(f"{base}/v1/sessions/{session_id}")
                response.raise_for_status()


if __name__ == "__main__":
    asyncio.run(main())
