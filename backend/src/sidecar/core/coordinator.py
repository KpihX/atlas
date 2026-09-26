from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

import yaml

from sidecar.config import PolicyConfig

from .models import BoardOperation, CoordinatorResult, Decision, MeetingState, Task, ToolRequest, now_iso
from .ports import GeneratorPort
from .tools import ToolRegistry

TaskHook = Callable[[Task], Awaitable[None]]


class Coordinator:
    def __init__(
        self,
        generator: GeneratorPort,
        tools: ToolRegistry,
        policy: PolicyConfig,
        utility_model: str | None = None,
    ) -> None:
        self.generator = generator
        self.tools = tools
        self.policy = policy
        self.utility_model = utility_model
        self.last_board_response = ""

    async def run(
        self,
        state: MeetingState,
        decision: Decision,
        *,
        on_task: TaskHook,
    ) -> CoordinatorResult:
        if not self.generator.available:
            return self._fallback(state, decision)
        messages = self._messages(state, decision)
        for _ in range(self.policy.max_tool_steps):
            response = await self.generator.generate(messages)
            result = self._parse(response.content, state, decision)
            if result.tool is None:
                return result
            task = Task(
                tool=result.tool.name,
                summary=result.working or f"Running {result.tool.name}",
                status="running",
            )
            await on_task(task)
            try:
                payload = await self.tools.execute(result.tool.name, result.tool.arguments)
                task.status = "done"
                task.result = payload
            except Exception as error:
                task.status = "failed"
                task.error = type(error).__name__
            task.completed_at = now_iso()
            await on_task(task)
            messages.extend(
                [
                    {"role": "assistant", "content": response.content},
                    {
                        "role": "user",
                        "content": "TOOL_RESULT\n"
                        + json.dumps(task.model_dump(mode="json"), ensure_ascii=False),
                    },
                ]
            )
        return CoordinatorResult(
            working="Tool loop stopped at configured limit.",
            board_ops=[
                BoardOperation(
                    action="create",
                    kind="finding",
                    title="Tool loop stopped",
                    body="The coordinator reached its configured tool-step limit.",
                    reason="tool safety limit reached",
                )
            ],
        )

    async def write_notes(self, state: MeetingState) -> str:
        new_transcript = state.transcript[state.notes_cursor :]
        if not self.generator.available or not new_transcript:
            return state.notes
        new_lines = [item.model_dump(mode="json") for item in new_transcript]
        messages = [
            {
                "role": "system",
                "content": (
                    "You maintain one living Markdown document for a working meeting. Rewrite the whole "
                    "document from the previous notes and only the new transcript lines. Keep earlier "
                    "subjects unless clearly superseded. Merge repetitions, sharpen evolving ideas, "
                    "preserve objections, unanswered questions, decisions and actions, and cite source "
                    "utterance IDs in brackets. Start with a title and a substantial current synthesis "
                    "written as 2 to 4 short thematic paragraphs of at most 3 sentences each. Synthesize "
                    "the current understanding; never replay the transcript chronologically. "
                    "The document may grow as understanding grows. Never remove a source-backed fact; "
                    "only consolidate exact repetition. Never invent. Return Markdown only."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {"previous_notes": state.notes, "new_transcript": new_lines}, ensure_ascii=False
                ),
            },
        ]
        response = await self.generator.generate(messages, self.utility_model)
        return response.content.strip() or state.notes

    async def name_session(self, state: MeetingState) -> str:
        if not self.generator.available or not state.transcript:
            return state.title
        response = await self.generator.generate(
            [
                {
                    "role": "system",
                    "content": (
                        "Give this evolving meeting a specific title in its current language using 3 to 8 "
                        "concrete words. Reflect the actual central subject now, not the opening greeting. "
                        "Return only the title, without quotes or punctuation."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "current_title": state.title,
                            "current_notes": state.notes[:4000],
                            "recent_transcript": [item.text for item in state.transcript[-12:]],
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            self.utility_model,
        )
        title = response.content.strip().splitlines()[0].strip(" `\"'.:-")
        return title[:80] or state.title

    async def curate_board(self, state: MeetingState) -> list[BoardOperation]:
        if not self.generator.available or not state.cards:
            return []
        messages = [
            {
                "role": "system",
                "content": (
                    "You curate a live meeting board. Return one JSON object with board_ops only. "
                    "Transform transcript-like cards into a small set of durable concepts. Merge semantic "
                    "duplicates, update weak titles and bodies, delete fully absorbed or useless cards, "
                    "and preserve distinct questions, decisions, suggestions and sourced findings. Every "
                    "update, merge or delete must reference existing card IDs. Never invent facts."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "cards": [item.model_dump(mode="json") for item in state.cards],
                        "schema": {
                            "board_ops": [
                                {
                                    "action": "create|update|merge|delete",
                                    "card_id": "existing id or null for create",
                                    "merge_ids": ["ids absorbed by merge target"],
                                    "kind": "idea|question|decision|suggestion|finding",
                                    "title": "durable title",
                                    "body": "concise current synthesis",
                                    "reason": "short justification",
                                }
                            ]
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = await self.generator.generate(messages, self.utility_model)
        self.last_board_response = response.content
        operations = self._parse(response.content, state, Decision(route="capture")).board_ops
        if not operations and self.utility_model is not None:
            response = await self.generator.generate(messages)
            self.last_board_response = response.content
            operations = self._parse(response.content, state, Decision(route="capture")).board_ops
        return operations

    def _messages(self, state: MeetingState, decision: Decision) -> list[dict[str, str]]:
        context = {
            "assistant_name": state.assistant_name,
            "language": state.language,
            "decision": decision.model_dump(mode="json"),
            "notes": state.notes,
            "recent_transcript": [item.model_dump(mode="json") for item in state.transcript[-16:]],
            "cards": [item.model_dump(mode="json") for item in state.cards],
            "recent_agent_speech": [
                {"text": item.text, "reason": item.reason, "status": item.status}
                for item in state.speeches[-6:]
            ],
            "recent_tasks": [
                {
                    "id": item.id,
                    "tool": item.tool,
                    "summary": item.summary,
                    "status": item.status,
                    "result": (
                        json.dumps(item.result, ensure_ascii=False)[:3000]
                        if item.result is not None
                        else None
                    ),
                    "error": item.error,
                }
                for item in state.tasks[-3:]
            ],
            "tools": self.tools.prompt_catalog(),
        }
        schema: dict[str, Any] = {
            "working": "short present-tense activity",
            "board_ops": [
                {
                    "action": "create|update|merge|delete",
                    "card_id": "existing target id, null only for create",
                    "merge_ids": ["other existing ids absorbed by target"],
                    "kind": "idea|question|decision|suggestion|finding",
                    "title": "specific stable concept",
                    "body": "current concise synthesis",
                    "reason": "why this operation improves the board",
                }
            ],
            "speech": "natural spoken answer of the useful length, or empty",
            "control": "none|mute|unmute|end_session",
            "tool": {"name": "registered tool name", "arguments": {}}
            if self.tools.prompt_catalog()
            else None,
        }
        return [
            {
                "role": "system",
                "content": (
                    "You are the resident meeting coordinator. Use only supplied state and tool results. "
                    "Curate the board as durable concepts, not transcript snippets. Prefer updating or "
                    "merging an existing card over creating a near-duplicate. Delete a card only when it is "
                    "obsolete or fully absorbed. A short or incomplete utterance normally needs no board "
                    "operation. Act like a capable member of the room, not a notification system. When "
                    "addressed, answer naturally from meeting context, prior speech, completed research, or "
                    "general knowledge; use a tool only when fresh external evidence is actually needed. "
                    "Handle follow-ups such as repeat, clarify, expand, correct, compare, or summarize from "
                    "their meaning and context without relying on a hard-coded intent list. When research "
                    "returns, speak its useful substance and sources rather than merely announcing that a "
                    "result exists. Match answer depth to the request: concise by default, detailed when the "
                    "room asks for detail. Use control=mute when asked to stay silent while continuing to "
                    "listen, control=unmute when invited to speak again, and control=end_session only for an "
                    "explicit request to end the meeting. Stopping the current sentence needs no persistent "
                    "control because barge-in already interrupts it. Reply in the meeting language. Do not "
                    "speak unless directly "
                    "addressed, returning a requested result, or correcting an imminent decision with fresh "
                    "evidence. Return only "
                    "one JSON object matching this schema: " + json.dumps(schema, ensure_ascii=False)
                ),
            },
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]

    @staticmethod
    def _parse(text: str, state: MeetingState, decision: Decision) -> CoordinatorResult:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        try:
            if start >= 0 and end > start:
                decoded: object = json.loads(cleaned[start : end + 1])
            else:
                decoded = yaml.safe_load(cleaned)
            if not isinstance(decoded, dict):
                raise TypeError
            raw = cast(dict[str, Any], decoded)
            board_ops = [
                BoardOperation.model_validate(item)
                for item in raw.get("board_ops", [])
                if isinstance(item, dict)
            ]
            if not board_ops and raw.get("card_title") and raw.get("card_body"):
                board_ops.append(
                    BoardOperation(
                        action="create",
                        kind=raw.get("card_kind", "finding"),
                        title=str(raw["card_title"]),
                        body=str(raw["card_body"]),
                        reason="legacy coordinator output",
                    )
                )
            tool = raw.get("tool")
            tool_request = (
                ToolRequest.model_validate(tool)
                if isinstance(tool, dict) and str(tool.get("name", "")).strip()
                else None
            )
            speech = str(raw.get("speech", "")).strip()
            if decision.addressed_probability < 0.5 and decision.route not in {"respond", "act"}:
                speech = ""
            return CoordinatorResult(
                working=str(raw.get("working", "")),
                board_ops=board_ops,
                speech=speech,
                control=raw.get("control", "none"),
                tool=tool_request,
            )
        except (ValueError, TypeError, json.JSONDecodeError, yaml.YAMLError):
            if decision.addressed_probability >= 0.5 or decision.route in {"respond", "act"}:
                return CoordinatorResult(speech=cleaned)
            return CoordinatorResult()

    @staticmethod
    def _fallback(state: MeetingState, decision: Decision) -> CoordinatorResult:
        if decision.addressed_probability >= 0.5:
            return CoordinatorResult(
                board_ops=[
                    BoardOperation(
                        action="create",
                        kind="finding",
                        title="Assistant unavailable",
                        body="The configured generator is unavailable; the request remains visible.",
                        reason="generator unavailable",
                    )
                ]
            )
        return CoordinatorResult()
