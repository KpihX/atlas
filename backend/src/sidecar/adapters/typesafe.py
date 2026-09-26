from __future__ import annotations

from typing import Any, Literal, cast

import httpx

from sidecar.config import DecisionConfig, secret
from sidecar.core.decisions import conservative_fallback
from sidecar.core.models import Decision, MeetingState


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

    async def evaluate(self, state: MeetingState, text: str) -> Decision:
        if self._secret is None:
            return conservative_fallback(state, text)
        payload = {
            "model": self.config.model,
            "state": {
                "assistant_name": state.assistant_name,
                "new_utterance": text,
                "recent_transcript": [item.text for item in state.transcript[-8:]],
                "current_notes": state.notes,
                "running_tasks": [item.summary for item in state.tasks if item.status == "running"],
            },
            "questions": {
                "addressed": {
                    "type": "noul",
                    "instructions": (
                        "Was the configured assistant directly asked or instructed in the new utterance?"
                    ),
                },
                "route": {
                    "type": "choice",
                    "instructions": "What should the meeting agent do next?",
                    "criteria": {
                        "ignore": "No useful agent work",
                        "capture": "Update notes or a visible card without speaking",
                        "investigate": "Start read-only background work",
                        "respond": "Prepare an answer for the room",
                        "act": "Use a registered tool for a direct request",
                        "control": "The utterance controls the assistant itself",
                    },
                },
                "salience": {
                    "type": "score",
                    "instructions": "How important is this utterance to the meeting?",
                    "criteria": ["Filler", "Useful", "Decision-critical"],
                },
                "speech_value": {
                    "type": "score",
                    "instructions": "How much value would a brief spoken intervention add?",
                    "criteria": ["No value", "Useful", "Decision-changing"],
                },
                "timing": {
                    "type": "choice",
                    "instructions": "When could a response still matter?",
                    "criteria": {"silent": None, "next_gap": None, "later": None},
                },
            },
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
            addressed = float(answers["addressed"]["noul"])
            route = str(answers["route"]["choice"])
            if addressed >= self.config.addressed_threshold and route in {"ignore", "capture"}:
                route = "respond"
            typed_route = cast(
                Literal["ignore", "capture", "investigate", "respond", "act", "control"], route
            )
            timing = cast(Literal["silent", "next_gap", "later"], str(answers["timing"]["choice"]))
            return Decision(
                route=typed_route,
                addressed_probability=addressed,
                salience=float(answers["salience"]["score"]),
                speech_value=float(answers["speech_value"]["score"]),
                timing=timing,
                rationale=f"Jev route={route}",
            )
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            return conservative_fallback(state, text)
