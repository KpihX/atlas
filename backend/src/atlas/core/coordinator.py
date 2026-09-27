from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from hashlib import sha256
from typing import Any, cast

import yaml

from atlas.config import PROMPTS, LLMRolesConfig, PolicyConfig

from .models import (
    AtlasState,
    BoardOperation,
    BoardProposal,
    Card,
    CoordinatorResult,
    Decision,
    NotesDocument,
    Task,
    ToolRequest,
)
from .notes import integrate_legacy_sources, integrate_sources, normalize_notes, parse_notes_document
from .ports import GeneratorPort
from .tools import ToolRegistry

TaskHook = Callable[[Task], Awaitable[None]]


class Coordinator:
    def __init__(
        self,
        generator: GeneratorPort,
        tools: ToolRegistry,
        policy: PolicyConfig,
        models: LLMRolesConfig,
    ) -> None:
        self.generator = generator
        self.tools = tools
        self.policy = policy
        self.models = models
        self.last_board_response = ""

    async def run(
        self,
        state: AtlasState,
        decision: Decision,
        *,
        on_task: TaskHook,
        mission_task: Task | None = None,
    ) -> CoordinatorResult:
        if not self.generator.available:
            return self._fallback(state, decision)
        messages = self._messages(state, decision)
        tool_attempted = False
        tool_completed = False
        for _ in range(self.policy.max_tool_steps):
            response = await self.generator.generate(messages, self.models.worker)
            result = self._parse(response.content, state, decision)
            if result.tool is None:
                if decision.route == "investigate" and not tool_attempted:
                    messages.extend(
                        [
                            {"role": "assistant", "content": response.content},
                            {
                                "role": "user",
                                "content": (
                                    "MISSION_CONTRACT\nThis mission requires external evidence. "
                                    "Select the best registered read tool and return its concrete arguments. "
                                    "Do not replace tool "
                                    "execution with a plan or a request for confirmation."
                                ),
                            },
                        ]
                    )
                    tool_attempted = True
                    continue
                if decision.route == "investigate" and mission_task is not None and not tool_completed:
                    mission_task.status = "failed"
                    mission_task.phase = "complete"
                    mission_task.error = "No registered research tool was selected"
                    await on_task(mission_task)
                return result
            tool_attempted = True
            mission_key = self._mission_key(result.tool)
            previous = next(
                (item for item in reversed(state.tasks) if item.mission_key == mission_key),
                None,
            )
            if previous is not None and previous.status in {"queued", "running"}:
                return CoordinatorResult(working=f"Already running: {previous.summary}")
            if previous is not None and previous.status == "done" and previous.result is not None:
                messages.extend(
                    [
                        {"role": "assistant", "content": response.content},
                        {
                            "role": "user",
                            "content": "EXISTING_TOOL_RESULT\n"
                            + json.dumps(previous.model_dump(mode="json"), ensure_ascii=False),
                        },
                    ]
                )
                continue
            task = mission_task or Task(tool=result.tool.name, summary=result.working)
            task.mission_key = mission_key
            task.tool = result.tool.name
            task.summary = result.working or f"Running {result.tool.name}"
            task.status = "running"
            task.phase = "executing"
            await on_task(task)
            try:
                payload = await self.tools.execute(result.tool.name, result.tool.arguments)
                task.result = payload
                task.phase = "synthesizing"
                tool_completed = True
            except Exception as error:
                task.status = "failed"
                task.phase = "complete"
                task.error = type(error).__name__
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

    @staticmethod
    def _mission_key(request: ToolRequest) -> str:
        canonical = json.dumps(
            {"tool": request.name, "arguments": request.arguments},
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode()).hexdigest()[:20]

    async def write_notes(self, state: AtlasState) -> NotesDocument:
        new_transcript = state.transcript[state.notes_cursor :]
        if not self.generator.available or not new_transcript:
            return state.notes_document
        new_lines = [item.model_dump(mode="json") for item in new_transcript]
        messages = [
            {
                "role": "system",
                "content": (PROMPTS.notes(state.identity_name)),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "previous_memory": state.notes_document.model_dump(mode="json"),
                        "legacy_notes": state.notes if not state.notes_document.source_ids else "",
                        "new_transcript": new_lines,
                        "tasks": [item.model_dump(mode="json") for item in state.tasks[-20:]],
                        "cards": [item.model_dump(mode="json") for item in state.cards[-20:]],
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = await self.generator.generate(messages, self.models.notes)
        try:
            candidate = parse_notes_document(response.content)
        except (ValueError, TypeError, yaml.YAMLError):
            return state.notes_document
        candidate = integrate_sources(candidate, new_transcript)
        candidate = integrate_legacy_sources(candidate, state.notes)
        return normalize_notes(candidate, state.tasks)

    async def name_session(self, state: AtlasState) -> str:
        if not self.generator.available or not state.transcript:
            return state.title
        response = await self.generator.generate(
            [
                {
                    "role": "system",
                    "content": (PROMPTS.naming(state.identity_name)),
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
            self.models.naming,
        )
        title = ""
        try:
            decoded = json.loads(response.content)
            if isinstance(decoded, dict):
                title = str(cast(dict[str, Any], decoded).get("title", ""))
        except json.JSONDecodeError:
            decoded = yaml.safe_load(response.content)
            if isinstance(decoded, dict):
                title = str(cast(dict[str, Any], decoded).get("title", ""))
        if not title:
            title = response.content.strip().splitlines()[0]
        title = title.strip(" `\"'.:-*#_")
        title = re.sub(r"\bassistant\b", state.identity_name, title, flags=re.IGNORECASE)
        if len(title) > 80:
            title = title[:81].rsplit(" ", 1)[0]
        return title or state.title

    async def curate_board(self, state: AtlasState) -> list[BoardOperation]:
        if not self.generator.available:
            return []
        messages = [
            {
                "role": "system",
                "content": (PROMPTS.board(state.identity_name)),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "cards": [item.model_dump(mode="json") for item in state.cards],
                        "shared_memory": state.notes_document.model_dump(mode="json"),
                        "recent_transcript": [
                            item.model_dump(mode="json") for item in state.transcript[-20:]
                        ],
                        "completed_tasks": [
                            item.model_dump(mode="json")
                            for item in state.tasks[-10:]
                            if item.status in {"done", "failed"}
                        ],
                        "schema": {
                            "desired_cards": [
                                {
                                    "kind": "idea|question|decision|suggestion|finding",
                                    "concept_key": "stable semantic identity for this one thesis",
                                    "title": "durable title",
                                    "body": "concise current synthesis",
                                    "source_card_ids": ["all existing card ids represented by this card"],
                                    "merge_evidence": {
                                        "same_resolution": True,
                                        "mutually_substitutable": True,
                                        "loses_independent_value": False,
                                        "rationale": "required only when combining multiple source cards",
                                    },
                                }
                            ],
                            "retirements": [
                                {
                                    "card_id": "explicitly obsolete existing card id",
                                    "reason": "why this concept no longer belongs on the live board",
                                }
                            ],
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        response = await self.generator.generate(messages, self.models.board)
        self.last_board_response = response.content
        try:
            desired = self._parse_board(response.content)
        except (ValueError, TypeError, json.JSONDecodeError, yaml.YAMLError):
            desired = None
        if desired is None and self.models.board != self.models.fallback:
            response = await self.generator.generate(messages, self.models.fallback)
            self.last_board_response = response.content
            try:
                desired = self._parse_board(response.content)
            except (ValueError, TypeError, json.JSONDecodeError, yaml.YAMLError):
                desired = None
        if desired is None:
            return []
        review_messages = [
            *messages,
            {"role": "assistant", "content": response.content},
            {"role": "user", "content": PROMPTS.board_review()},
        ]
        reviewed = await self.generator.generate(review_messages, self.models.board)
        self.last_board_response = reviewed.content
        with suppress(ValueError, TypeError, json.JSONDecodeError, yaml.YAMLError):
            desired = self._parse_board(reviewed.content)
        return self.reconcile_board(state.cards, desired)

    @staticmethod
    def _parse_board(text: str) -> BoardProposal:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        start, end = cleaned.find("{"), cleaned.rfind("}")
        decoded: object = (
            json.loads(cleaned[start : end + 1]) if start >= 0 and end > start else yaml.safe_load(cleaned)
        )
        if not isinstance(decoded, dict):
            raise TypeError("Board response must be an object")
        proposal = BoardProposal.model_validate(decoded)
        concept_keys = [concept.concept_key for concept in proposal.desired_cards]
        if len(concept_keys) != len(set(concept_keys)):
            raise ValueError("Board response contains duplicate concept keys")
        claimed_ids = [card_id for concept in proposal.desired_cards for card_id in concept.source_card_ids]
        if len(claimed_ids) != len(set(claimed_ids)):
            raise ValueError("An existing card cannot feed multiple desired concepts")
        return proposal

    @staticmethod
    def reconcile_board(existing: list[Card], proposal: BoardProposal) -> list[BoardOperation]:
        existing_by_id = {card.id: card for card in existing}
        claimed: set[str] = set()
        operations: list[BoardOperation] = []
        for concept in proposal.desired_cards:
            source_ids = list(
                dict.fromkeys(
                    card_id
                    for card_id in concept.source_card_ids
                    if card_id in existing_by_id and card_id not in claimed
                )
            )
            if len(source_ids) > 1 and (
                concept.merge_evidence is None or not concept.merge_evidence.proves_equivalence
            ):
                continue
            if not source_ids:
                matching = next(
                    (
                        card.id
                        for card in existing
                        if card.id not in claimed and card.concept_key == concept.concept_key
                    ),
                    None,
                )
                if matching is not None:
                    source_ids = [matching]
            if not source_ids:
                operations.append(
                    BoardOperation(
                        action="create",
                        kind=concept.kind,
                        concept_key=concept.concept_key,
                        title=concept.title,
                        body=concept.body,
                        reason="new durable concept in desired board",
                    )
                )
                continue
            claimed.update(source_ids)
            target = existing_by_id[source_ids[0]]
            changed = (
                target.kind != concept.kind
                or target.concept_key != concept.concept_key
                or target.title != concept.title
                or target.body != concept.body
            )
            if len(source_ids) > 1:
                operations.append(
                    BoardOperation(
                        action="merge",
                        card_id=target.id,
                        merge_ids=source_ids[1:],
                        kind=concept.kind,
                        concept_key=concept.concept_key,
                        title=concept.title,
                        body=concept.body,
                        reason="desired board combines semantically equivalent cards",
                    )
                )
            elif changed:
                operations.append(
                    BoardOperation(
                        action="update",
                        card_id=target.id,
                        kind=concept.kind,
                        concept_key=concept.concept_key,
                        title=concept.title,
                        body=concept.body,
                        reason="desired board refines the existing concept",
                    )
                )
        for retirement in proposal.retirements:
            if retirement.card_id in existing_by_id and retirement.card_id not in claimed:
                operations.append(
                    BoardOperation(
                        action="delete",
                        card_id=retirement.card_id,
                        reason=retirement.reason,
                    )
                )
        return operations

    def _messages(self, state: AtlasState, decision: Decision) -> list[dict[str, str]]:
        context = {
            "identity_name": state.identity_name,
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
                    "concept_key": "stable semantic identity for this one thesis",
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
                    PROMPTS.worker(
                        state.identity_name,
                        json.dumps(schema, ensure_ascii=False),
                    )
                ),
            },
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ]

    @staticmethod
    def _parse(text: str, state: AtlasState, decision: Decision) -> CoordinatorResult:
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
            tool_object = cast(dict[str, object], tool) if isinstance(tool, dict) else None
            tool_request = (
                ToolRequest.model_validate(tool_object)
                if tool_object is not None and str(tool_object.get("name", "")).strip()
                else None
            )
            speech = str(raw.get("speech", "")).strip()
            if decision.addressee not in {"atlas", "room"}:
                speech = ""
            return CoordinatorResult(
                working=str(raw.get("working", "")),
                board_ops=board_ops,
                speech=speech,
                control=raw.get("control", "none"),
                tool=tool_request,
            )
        except (ValueError, TypeError, json.JSONDecodeError, yaml.YAMLError):
            if decision.addressee in {"atlas", "room"}:
                return CoordinatorResult(speech=cleaned)
            return CoordinatorResult()

    @staticmethod
    def _fallback(state: AtlasState, decision: Decision) -> CoordinatorResult:
        if decision.addressee in {"atlas", "room"}:
            return CoordinatorResult(
                board_ops=[
                    BoardOperation(
                        action="create",
                        kind="finding",
                        title="Atlas unavailable",
                        body="The configured generator is unavailable; the request remains visible.",
                        reason="generator unavailable",
                    )
                ]
            )
        return CoordinatorResult()
