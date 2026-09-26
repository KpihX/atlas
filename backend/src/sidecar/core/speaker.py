from __future__ import annotations

import json
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict

from .models import Decision, MeetingState, Task, Utterance
from .ports import GeneratorPort
from .voice import spoken_text


class SpeakerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    speech: str = ""
    control: Literal["none", "mute", "unmute", "end_session"] = "none"


class SpeakerAgent:
    def __init__(self, generator: GeneratorPort, model_id: str | None = None) -> None:
        self.generator = generator
        self.model_id = model_id

    @property
    def available(self) -> bool:
        return self.generator.available

    async def answer(
        self,
        state: MeetingState,
        utterance: Utterance,
        decision: Decision,
    ) -> SpeakerResult:
        return await self._speak(
            state,
            {
                "kind": "participant",
                "utterance": utterance.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
            },
        )

    async def report_task(self, state: MeetingState, task: Task) -> SpeakerResult:
        return await self._speak(
            state,
            {
                "kind": "task_progress",
                "task": {
                    "tool": task.tool,
                    "summary": task.summary,
                    "status": task.status,
                    "error": task.error,
                    "result": self._compact_result(task.result),
                },
            },
        )

    async def _speak(self, state: MeetingState, trigger: dict[str, Any]) -> SpeakerResult:
        if not self.generator.available:
            return SpeakerResult()
        running = [
            {"agent": item.agent, "summary": item.summary, "elapsed": item.duration_ms}
            for item in state.agent_runs
            if item.status == "running"
        ]
        tasks = [
            {
                "tool": item.tool,
                "summary": item.summary,
                "status": item.status,
                "error": item.error,
                "result": self._compact_result(item.result),
            }
            for item in state.tasks[-5:]
        ]
        context = {
            "language": state.language,
            "voice_mode": state.voice_mode,
            "trigger": trigger,
            "recent_conversation": [
                {"speaker": item.speaker, "text": item.text} for item in state.transcript[-8:]
            ],
            "recent_speech": [item.text for item in state.speeches[-3:]],
            "current_synthesis": state.notes[:2500],
            "running_agents": running,
            "tasks": tasks,
        }
        response = await self.generator.generate(
            [
                {
                    "role": "system",
                    "content": (
                        "You are the always-available voice of a meeting agent. You have no tools and never "
                        "wait for workers. Answer immediately from shared memory. If work is running, "
                        "explain "
                        "what is running, what is already known, and what remains. For task_started, briefly "
                        "acknowledge the work. For task_done, give the useful substance, not a notification. "
                        "Be natural, social and easy to hear. Never output JSON, URLs, tool syntax, "
                        "identifiers "
                        "or markdown inside speech. Return only JSON matching "
                        '{"speech":"spoken prose","control":"none|mute|unmute|end_session"}.'
                    ),
                },
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            self.model_id,
        )
        return self._parse(response.content)

    @staticmethod
    def _parse(text: str) -> SpeakerResult:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            value: object = json.loads(cleaned)
        except json.JSONDecodeError:
            value = yaml.safe_load(cleaned)
        if not isinstance(value, dict):
            return SpeakerResult(speech=spoken_text(cleaned))
        raw = cast(dict[str, Any], value)
        return SpeakerResult(
            speech=spoken_text(str(raw.get("speech", ""))),
            control=raw.get("control", "none"),
        )

    @staticmethod
    def _compact_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is None:
            return None
        rows = result.get("results")
        if not isinstance(rows, list):
            return {key: value for key, value in result.items() if key not in {"raw", "content"}}
        return {
            "result_count": len(rows),
            "sources": [
                {
                    "title": item.get("title", ""),
                    "highlights": item.get("highlights", [])[:2],
                }
                for item in rows[:5]
                if isinstance(item, dict)
            ],
        }
