from __future__ import annotations

from typing import Any, Literal, cast

import httpx

from atlas.config import PROMPTS, DecisionConfig, secret
from atlas.core.decisions import conservative_fallback
from atlas.core.models import AtlasState, Decision


class TypeSafeDecision:
    def __init__(self, config: DecisionConfig, transport: httpx.AsyncClient | None = None) -> None:
        self.config = config
        self._secret = secret(config.secret_env)
        self._transport = transport or httpx.AsyncClient(timeout=10)
        self._owns_transport = transport is None

    @property
    def available(self) -> bool:
        return self._secret is not None

    async def close(self) -> None:
        if self._owns_transport:
            await self._transport.aclose()

    async def evaluate(self, state: AtlasState, text: str) -> Decision:
        if self._secret is None:
            return conservative_fallback(state, text)
        payload = {
            "model": self.config.model,
            "state": {
                "identity_name": state.identity_name,
                "new_utterance": text,
                "recent_transcript": [
                    {"speaker": item.speaker, "text": item.text} for item in state.transcript[-12:]
                ],
                "shared_memory": state.notes_document.model_dump(mode="json"),
                "tasks": [
                    {
                        "tool": item.tool,
                        "summary": item.summary,
                        "status": item.status,
                        "error": item.error,
                    }
                    for item in state.tasks[-20:]
                ],
                "running_agents": [
                    {"agent": item.agent, "summary": item.summary}
                    for item in state.agent_runs
                    if item.status == "running"
                ],
                "recent_decisions": [
                    {
                        "route": item.result.route,
                        "addressee": item.result.addressee,
                        "initiative": item.result.initiative,
                        "timing": item.result.timing,
                    }
                    for item in state.decisions[-5:]
                ],
                "room": {
                    "floor_busy": state.floor_busy,
                    "partial_speech": state.partial,
                    "voice_mode": state.voice_mode,
                    "session_status": state.session_status,
                    "inactivity_probability": state.pipeline.stt_inactivity_probability,
                    "expected_speaker_latency_ms": self.config.expected_speaker_latency_ms,
                    "queued_speeches": [
                        {"reason": item.reason, "status": item.status}
                        for item in state.speeches[-5:]
                        if item.status in {"waiting_gap", "authorized", "playing"}
                    ],
                },
            },
            "questions": PROMPTS.jev_questions(state.identity_name),
        }
        try:
            response = await self._transport.post(
                self.config.endpoint,
                headers={"Authorization": f"Bearer {self._secret}", "Content-Type": "application/json"},
                json=payload,
            )
            response.raise_for_status()
            raw: Any = response.json()
            answers = raw["answers"]
            route = str(answers["route"]["choice"])
            typed_route = cast(
                Literal["ignore", "capture", "investigate", "respond", "act", "control"], route
            )
            timing = cast(Literal["silent", "next_gap", "later"], str(answers["timing"]["choice"]))
            return Decision(
                route=typed_route,
                addressee=cast(
                    Literal["atlas", "another_participant", "room", "uncertain"],
                    str(answers["addressee"]["choice"]),
                ),
                memory=cast(Literal["ignore", "capture"], str(answers["memory"]["choice"])),
                initiative=cast(
                    Literal["none", "assigned", "proactive"],
                    str(answers["initiative"]["choice"]),
                ),
                speech_depth=cast(
                    Literal["silent", "brief", "normal", "deep"],
                    str(answers["speech_depth"]["choice"]),
                ),
                timing=timing,
                rationale=f"Jev route={route}",
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return conservative_fallback(state, text)
