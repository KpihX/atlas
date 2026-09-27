from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from time import monotonic
from typing import Any, cast

import yaml

from atlas.config import PROMPTS

from .models import AtlasState, Decision, SpeechPlan, Task, Utterance
from .ports import GeneratorPort
from .voice import bounded_spoken_turn, spoken_text

logger = logging.getLogger("uvicorn.error").getChild("speaker")
SpeechChunkCallback = Callable[[str], Awaitable[None]]


class SpeakerAgent:
    def __init__(self, generator: GeneratorPort, model_id: str | None = None) -> None:
        self.generator = generator
        self.model_id = model_id

    @property
    def available(self) -> bool:
        return self.generator.available

    async def answer(
        self,
        state: AtlasState,
        utterance: Utterance,
        decision: Decision,
    ) -> SpeechPlan:
        return await self._speak(
            state,
            {
                "kind": "participant",
                "utterance": utterance.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
            },
        )

    async def acknowledge_mission(self, state: AtlasState, task: Task) -> SpeechPlan:
        return await self._speak(
            state,
            {
                "kind": "task_started",
                "task": {"summary": task.summary, "status": task.status, "phase": task.phase},
            },
        )

    async def report_task(self, state: AtlasState, task: Task) -> SpeechPlan:
        return await self._speak(
            state,
            {
                "kind": "task_done" if task.phase in {"reporting", "complete"} else "task_progress",
                "task": {
                    "tool": task.tool,
                    "summary": task.summary,
                    "status": task.status,
                    "phase": task.phase,
                    "error": task.error,
                    "result": self._compact_result(task.result),
                },
            },
        )

    async def stream_answer(
        self,
        state: AtlasState,
        utterance: Utterance,
        decision: Decision,
        on_phrase: SpeechChunkCallback,
    ) -> SpeechPlan:
        trigger: dict[str, Any] = {
            "kind": "participant",
            "utterance": utterance.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
        }
        messages = [
            {
                "role": "system",
                "content": (PROMPTS.speaker_stream(state.identity_name)),
            },
            {"role": "user", "content": json.dumps(self._context(state, trigger), ensure_ascii=False)},
        ]
        started = monotonic()
        pending = ""
        delivered: list[str] = []
        undecided = True
        phrase_limit = {"silent": 0, "brief": 2, "normal": 4, "deep": 7}[decision.speech_depth]
        async for token in self.generator.stream(messages, self.model_id):
            pending += token
            if undecided:
                probe = pending.strip().upper()
                if "<SILENT>".startswith(probe):
                    continue
                if probe.startswith("<SILENT>"):
                    return SpeechPlan(speak=False)
                undecided = False
            while True:
                phrase, pending = self._take_phrase(pending)
                if not phrase:
                    break
                if len(delivered) >= phrase_limit:
                    pending = ""
                    break
                await on_phrase(phrase)
                delivered.append(phrase)
            if len(delivered) >= phrase_limit:
                break
        if undecided and pending.strip().upper().startswith("<SILENT>"):
            return SpeechPlan(speak=False)
        if pending.strip() and len(delivered) < phrase_limit:
            final_phrase = pending.strip()
            if final_phrase[-1:] not in ".?!;:":
                final_phrase += "."
            await on_phrase(final_phrase)
            delivered.append(final_phrase)
        logger.info("speaker.streamed latency_ms=%d", round((monotonic() - started) * 1000))
        speech = spoken_text(" ".join(delivered))
        return SpeechPlan(speak=bool(speech), spoken_core=speech, control="none")

    async def _speak(self, state: AtlasState, trigger: dict[str, Any]) -> SpeechPlan:
        if not self.generator.available:
            return SpeechPlan()
        context = self._context(state, trigger)

        response = await self.generator.generate(
            [
                {
                    "role": "system",
                    "content": (
                        PROMPTS.speaker_structured(
                            state.identity_name,
                            json.dumps(
                                {
                                    "speak": "true|false",
                                    "spoken_core": "concise spoken prose",
                                    "visual_detail": "optional detail for Notes and Board, never spoken",
                                    "control": "none|mute|unmute|end_session",
                                }
                            ),
                        )
                    ),
                },
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
            ],
            self.model_id,
        )
        result = self._parse(response.content)
        kind = str(trigger.get("kind", "participant"))
        sentence_limit = 1 if kind == "task_started" else 4
        result.spoken_core = bounded_spoken_turn(result.spoken_core, sentence_limit)
        result.speak = result.speak and bool(result.spoken_core)
        return result

    @staticmethod
    def _context(state: AtlasState, trigger: dict[str, Any]) -> dict[str, Any]:
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
                "result": SpeakerAgent._compact_result(item.result),
            }
            for item in state.tasks[-5:]
        ]
        return {
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

    @staticmethod
    def _take_phrase(text: str) -> tuple[str, str]:
        for index, character in enumerate(text):
            if character in ".?!;:" and index >= 15:
                return text[: index + 1].strip(), text[index + 1 :].lstrip()
        return "", text

    @staticmethod
    def _parse(text: str) -> SpeechPlan:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            value: object = json.loads(cleaned)
        except json.JSONDecodeError:
            value = yaml.safe_load(cleaned)
        if not isinstance(value, dict):
            speech = spoken_text(cleaned)
            return SpeechPlan(speak=bool(speech), spoken_core=speech)
        raw = cast(dict[str, Any], value)
        speech = spoken_text(str(raw.get("spoken_core", raw.get("speech", ""))))
        return SpeechPlan(
            speak=bool(raw.get("speak", bool(speech))),
            spoken_core=speech,
            visual_detail=str(raw.get("visual_detail", "")),
            control=raw.get("control", "none"),
        )

    @staticmethod
    def _compact_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
        if result is None:
            return None
        rows_value: object = result.get("results")
        if not isinstance(rows_value, list):
            return {key: value for key, value in result.items() if key not in {"raw", "content"}}
        rows = cast(list[object], rows_value)
        sources: list[dict[str, object]] = []
        for item_value in rows[:3]:
            if not isinstance(item_value, dict):
                continue
            item = cast(dict[str, object], item_value)
            highlights_value = item.get("highlights", [])
            highlights = cast(list[object], highlights_value) if isinstance(highlights_value, list) else []
            sources.append(
                {
                    "title": str(item.get("title", ""))[:160],
                    "highlights": [str(value)[:240] for value in highlights[:1]],
                }
            )
        return {
            "result_count": len(rows),
            "sources": sources,
        }
