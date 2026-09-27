# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest

from atlas.config import AppConfig, ProductConfig, default_config_text
from atlas.core.coordinator import Coordinator
from atlas.core.engine import AtlasEngine
from atlas.core.models import (
    AtlasState,
    AudioResult,
    BoardOperation,
    Card,
    Decision,
    LLMResult,
    ProcessStatus,
    SessionStatus,
    SessionSummary,
    Speech,
)
from atlas.core.speaker import SpeakerAgent
from atlas.core.tools import ToolRegistry, ToolSpec


class MemoryStore:
    states: dict[str, AtlasState]

    def __init__(self) -> None:
        self.states = {}

    async def open(self) -> None:
        pass

    async def load(self, session_id: str) -> AtlasState | None:
        state = self.states.get(session_id)
        return state.model_copy(deep=True) if state else None

    async def list_sessions(self) -> list[SessionSummary]:
        return [
            SessionSummary(
                session_id=state.session_id or "",
                title=state.title,
                status=state.session_status,
                preview=state.transcript[-1].text if state.transcript else "",
                utterance_count=len(state.transcript),
                created_at=state.created_at,
                updated_at=state.updated_at,
            )
            for state in self.states.values()
            if state.session_id
        ]

    async def save(self, state: AtlasState) -> None:
        if state.session_id:
            self.states[state.session_id] = state.model_copy(deep=True)

    async def delete(self, session_id: str) -> bool:
        return self.states.pop(session_id, None) is not None

    async def close(self) -> None:
        pass


class FakeDecision:
    available = True

    def __init__(
        self,
        route: Literal["ignore", "capture", "investigate", "respond", "act", "control"] = "respond",
        addressed: bool = True,
        initiative: Literal["none", "assigned", "proactive"] | None = None,
    ) -> None:
        self.route: Literal["ignore", "capture", "investigate", "respond", "act", "control"] = route
        self.addressed = addressed
        self.initiative: Literal["none", "assigned", "proactive"] = initiative or (
            "assigned" if route in {"investigate", "act"} else "none"
        )

    async def evaluate(self, state: AtlasState, text: str) -> Decision:
        return Decision(
            route=self.route,
            addressee="atlas" if self.addressed else "another_participant",
            memory="capture",
            initiative=self.initiative,
            speech_depth="brief" if self.addressed else "silent",
            timing="next_gap",
        )


class FakeGenerator:
    available = True

    async def generate(self, messages: list[dict[str, str]], model_id: str | None = None) -> LLMResult:
        if "Give this evolving session" in messages[0]["content"]:
            return LLMResult(content="Answer Review", model="fake", provider="fake")
        if "structured shared memory" in messages[0]["content"]:
            return LLMResult(
                content=json.dumps(
                    {
                        "synthesis": ["Living notes"],
                        "participants": [],
                        "topics": ["Answer review"],
                        "hypotheses": [],
                        "questions": [],
                        "decisions": [],
                        "actions": [],
                        "current_work": [],
                        "source_ids": [],
                    }
                ),
                model="fake",
                provider="fake",
            )
        return LLMResult(
            content=json.dumps(
                {
                    "working": "Answering",
                    "card_kind": "finding",
                    "card_title": "Answer",
                    "card_body": "Grounded result",
                    "speech": "Here is the answer.",
                    "tool": None,
                }
            ),
            model="fake",
            provider="fake",
        )

    async def stream(self, messages: list[dict[str, str]], model_id: str | None = None) -> AsyncIterator[str]:
        yield "Here is the answer."


class SilentGenerator(FakeGenerator):
    async def stream(self, messages: list[dict[str, str]], model_id: str | None = None) -> AsyncIterator[str]:
        yield "<SILENT>"


class ResearchGenerator(FakeGenerator):
    async def generate(self, messages: list[dict[str, str]], model_id: str | None = None) -> LLMResult:
        if "background missions" not in messages[0]["content"]:
            return await super().generate(messages, model_id)
        if any("TOOL_RESULT" in message["content"] for message in messages):
            return LLMResult(
                content=json.dumps(
                    {
                        "working": "Research integrated",
                        "board_ops": [],
                        "speech": "",
                        "control": "none",
                        "tool": None,
                    }
                ),
                model="fake",
                provider="fake",
            )
        return LLMResult(
            content=json.dumps(
                {
                    "working": "Researching sources",
                    "board_ops": [],
                    "speech": "",
                    "control": "none",
                    "tool": {"name": "fake_search", "arguments": {"query": "situation"}},
                }
            ),
            model="fake",
            provider="fake",
        )


class BlockingGenerator(FakeGenerator):
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def stream(self, messages: list[dict[str, str]], model_id: str | None = None) -> AsyncIterator[str]:
        self.started.set()
        await asyncio.Event().wait()
        yield "unreachable"


class SequenceDecision(FakeDecision):
    def __init__(self, decisions: list[Decision]) -> None:
        super().__init__()
        self.decisions = decisions

    async def evaluate(self, state: AtlasState, text: str) -> Decision:
        return self.decisions.pop(0)


class FakeSTT:
    available = False
    connected = False

    async def start(self, on_partial: Any, on_final: Any, on_event: Any, language: str) -> None:
        pass

    async def send(self, audio: bytes) -> None:
        pass

    async def flush(self) -> None:
        pass

    async def stop(self) -> None:
        pass


class FakeTTS:
    available = True

    async def synthesize(
        self,
        text: str,
        language: str,
        padding_bonus: float | None = None,
        temp: float | None = None,
    ) -> AudioResult:
        return AudioResult(
            data_base64=base64.b64encode(b"\0\0").decode(), format="pcm_24000", sample_rate=24000
        )

    async def stream(self, text: str, language: str, on_chunk: Any, on_text: Any = None) -> None:
        if on_text is not None:
            await on_text(text, 0.0, 0.1)
        await on_chunk(b"\0\0", 24000, "pcm_24000")

    async def stream_chunks(
        self, text_chunks: AsyncIterator[str], language: str, on_chunk: Any, on_text: Any = None
    ) -> None:
        async for text in text_chunks:
            if on_text is not None:
                await on_text(text, 0.0, 0.1)
            await on_chunk(b"\0\0", 24000, "pcm_24000")


class FailingTTS(FakeTTS):
    async def stream_chunks(
        self, text_chunks: AsyncIterator[str], language: str, on_chunk: Any, on_text: Any = None
    ) -> None:
        async for _ in text_chunks:
            raise RuntimeError("provider stream closed")


class TrackingSTT(FakeSTT):
    available = True

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.languages: list[str] = []

    @property
    def connected(self) -> bool:
        return self.starts > self.stops

    async def start(self, on_partial: Any, on_final: Any, on_event: Any, language: str) -> None:
        self.starts += 1
        self.languages.append(language)

    async def stop(self) -> None:
        self.stops += 1


def build_engine(
    decision: FakeDecision,
    generator: FakeGenerator,
    published: list[dict[str, object]],
    tts: Any | None = None,
    tools: ToolRegistry | None = None,
) -> AtlasEngine:
    config = AppConfig.model_validate(json.loads(default_config_text()))
    config = config.model_copy(
        update={"session": config.session.model_copy(update={"notes_interval_seconds": 3600})}
    )

    async def publish(message: dict[str, object]) -> None:
        published.append(message)

    return AtlasEngine(
        product=ProductConfig(
            project_id="atlas", display_name="Atlas", companion_name="Atlas", protocol_version=1
        ),
        config=config,
        store=MemoryStore(),
        decision=decision,
        speaker=SpeakerAgent(generator),
        coordinator=Coordinator(generator, tools or ToolRegistry(1), config.policy, config.llm.roles),
        stt=FakeSTT(),
        tts=tts or FakeTTS(),
        publish=publish,
        tool_health={},
    )


def test_coordinator_accepts_zen_yaml_shape() -> None:
    state = AtlasState(project_id="test", protocol_version=1)
    decision = Decision(route="respond", addressee="atlas")
    result = Coordinator._parse(
        "speech: Bonjour\ncard_title: Test\ncard_body: OK\ncard_kind: finding\nworking: Done\ntool: null",
        state,
        decision,
    )
    assert result.speech == "Bonjour"
    assert result.board_ops[0].title == "Test"


def test_coordinator_ignores_empty_tool_name() -> None:
    state = AtlasState(project_id="test", protocol_version=1)
    decision = Decision(route="respond", addressee="atlas")
    result = Coordinator._parse(
        '{"speech":"Bonjour","tool":{"name":"","arguments":{}}}',
        state,
        decision,
    )
    assert result.tool is None


@pytest.mark.asyncio
async def test_direct_turn_reaches_card_and_authorized_speech() -> None:
    config = AppConfig.model_validate(json.loads(default_config_text()))
    config = config.model_copy(
        update={
            "session": config.session.model_copy(update={"notes_interval_seconds": 3600}),
            "policy": config.policy.model_copy(
                update={"stable_floor_gap_seconds": 0, "wait_to_visual_fallback_seconds": 0.1}
            ),
        }
    )
    published: list[dict[str, object]] = []

    async def publish(message: dict[str, object]) -> None:
        published.append(message)

    generator = FakeGenerator()
    coordinator = Coordinator(generator, ToolRegistry(1), config.policy, config.llm.roles)
    engine = AtlasEngine(
        product=ProductConfig(
            project_id="test", display_name="Atlas", companion_name="Atlas", protocol_version=1
        ),
        config=config,
        store=MemoryStore(),
        decision=FakeDecision(),
        speaker=SpeakerAgent(generator),
        coordinator=coordinator,
        stt=FakeSTT(),
        tts=FakeTTS(),
        publish=publish,
        tool_health={},
    )
    await engine.start()
    await engine.start_session({})
    cue = AsyncMock()
    engine._publish_presence_cue = cue
    worker_run = await engine._start_agent("worker", "Background research")
    reporting_run = await engine._start_agent("speaker", "Background progress report")
    await engine.drain()
    cue.assert_not_awaited()
    await engine._finish_agent(worker_run, "done")
    await engine._finish_agent(reporting_run, "done")
    await engine.commit_utterance("Assistant, what is the answer?", source="manual")
    await engine.drain()
    assert engine.state.title == "Answer Review"
    cue.assert_awaited_once()
    subtitles = [message for message in published if message.get("type") == "speech.subtitle"]
    assert any(message.get("text") == "Here is the answer." for message in subtitles)
    assert any(message.get("start_s") == 0.0 and message.get("stop_s") == 0.1 for message in subtitles)
    assert any(message.get("type") == "speech.authorized" for message in published)
    assert any(message.get("type") == "speech.audio.chunk" for message in published)
    assert any(message.get("type") == "speech.audio.end" for message in published)
    await engine._write_notes()
    assert engine.state.notes_version == 1
    assert engine.state.notes_cursor == len(engine.state.transcript)
    await engine.stop()


@pytest.mark.asyncio
async def test_sessions_are_isolated_and_resumable() -> None:
    config = AppConfig.model_validate(json.loads(default_config_text()))
    config = config.model_copy(
        update={"session": config.session.model_copy(update={"notes_interval_seconds": 3600})}
    )

    async def publish(_: dict[str, object]) -> None:
        pass

    store = MemoryStore()
    engine = AtlasEngine(
        product=ProductConfig(
            project_id="test", display_name="Atlas", companion_name="Atlas", protocol_version=1
        ),
        config=config,
        store=store,
        decision=FakeDecision(),
        speaker=SpeakerAgent(FakeGenerator()),
        coordinator=Coordinator(FakeGenerator(), ToolRegistry(1), config.policy, config.llm.roles),
        stt=FakeSTT(),
        tts=FakeTTS(),
        publish=publish,
        tool_health={},
    )
    await engine.start()
    await engine.start_session({})
    first_id = engine.state.session_id
    await engine.commit_utterance("Assistant, preserve this session", source="manual")
    await engine.drain()
    await engine.start_session({})
    second_id = engine.state.session_id
    assert engine.state.process_status == ProcessStatus.READY
    assert first_id and second_id and first_id != second_id
    assert len(await engine.list_sessions()) == 2
    assert await engine.open_session(first_id)
    assert store.states[second_id].session_status == SessionStatus.PAUSED
    assert engine.state.transcript[-1].text == "Assistant, preserve this session"
    assert engine.state.title == "Answer Review"
    assert await engine.rename_session(first_id, "Renamed session")
    assert "# Renamed session" in (await engine.export_session(first_id) or "")
    engine.state.cards = [
        Card(id="card-a", title="Medical AI", body="Initial", source_ids=["utt-a"]),
        Card(id="card-b", title="Health agents", body="Related", source_ids=["utt-b"]),
    ]
    await engine._apply_board_operations(
        [
            BoardOperation(
                action="merge",
                card_id="card-a",
                merge_ids=["card-b"],
                title="Agentic healthcare",
                body="Merged and refined",
                reason="same durable concept",
            )
        ],
        "utt-c",
    )
    assert [card.id for card in engine.state.cards] == ["card-a"]
    assert engine.state.cards[0].title == "Agentic healthcare"
    assert set(engine.state.cards[0].source_ids) == {"utt-a", "utt-b", "utt-c"}
    assert await engine.delete_session(second_id)
    assert second_id not in store.states
    await engine.stop()


@pytest.mark.asyncio
async def test_stt_lifetime_follows_capture_session() -> None:
    config = AppConfig.model_validate(json.loads(default_config_text()))
    config = config.model_copy(
        update={"session": config.session.model_copy(update={"notes_interval_seconds": 3600})}
    )

    async def publish(_: dict[str, object]) -> None:
        pass

    stt = TrackingSTT()
    engine = AtlasEngine(
        product=ProductConfig(
            project_id="test", display_name="Atlas", companion_name="Atlas", protocol_version=1
        ),
        config=config,
        store=MemoryStore(),
        decision=FakeDecision(),
        speaker=SpeakerAgent(FakeGenerator()),
        coordinator=Coordinator(FakeGenerator(), ToolRegistry(1), config.policy, config.llm.roles),
        stt=stt,
        tts=FakeTTS(),
        publish=publish,
        tool_health={},
    )
    await engine.start()
    assert stt.starts == 0
    await engine.start_session({})
    assert stt.starts == 1
    assert stt.languages == ["en"]
    await engine.set_language("fr")
    assert stt.starts == 2
    assert stt.stops == 1
    assert stt.languages == ["en", "fr"]
    await engine.client_disconnected()
    assert stt.stops == 2
    await engine.stop()


@pytest.mark.asyncio
async def test_capture_turn_does_not_start_worker_or_speaker() -> None:
    published: list[dict[str, object]] = []
    engine = build_engine(FakeDecision(route="capture", addressed=False), FakeGenerator(), published)
    await engine.start()
    await engine.start_session({})
    await engine.commit_utterance("Maybe we could", source="manual")
    await engine.drain()
    assert not any(run.agent in {"worker", "speaker"} for run in engine.state.agent_runs)
    assert not any(message.get("type") == "speech.authorized" for message in published)
    await engine.stop()


@pytest.mark.asyncio
async def test_response_to_another_person_does_not_start_speaker() -> None:
    published: list[dict[str, object]] = []
    engine = build_engine(FakeDecision(route="respond", addressed=False), FakeGenerator(), published)
    await engine.start()
    await engine.start_session({})
    await engine.commit_utterance("Pavel, what do you think?", source="manual")
    await engine.drain()
    assert not any(run.agent == "speaker" for run in engine.state.agent_runs)
    assert not any(message.get("type") == "speech.authorized" for message in published)
    await engine.stop()


@pytest.mark.asyncio
async def test_response_offered_to_room_can_start_speaker_as_a_room_member() -> None:
    published: list[dict[str, object]] = []
    decision = SequenceDecision(
        [
            Decision(
                route="respond",
                addressee="room",
                memory="capture",
                speech_depth="brief",
                timing="next_gap",
            )
        ]
    )
    engine = build_engine(decision, FakeGenerator(), published)
    await engine.start()
    await engine.start_session({})
    await engine.commit_utterance("What does everyone think?", source="manual")
    await engine.drain()
    assert any(run.agent == "speaker" for run in engine.state.agent_runs)
    await engine.stop()


@pytest.mark.asyncio
async def test_speaker_second_stage_veto_suppresses_audio() -> None:
    published: list[dict[str, object]] = []
    engine = build_engine(FakeDecision(route="respond", addressed=True), SilentGenerator(), published)
    await engine.start()
    await engine.start_session({})
    await engine.commit_utterance("Atlas is an interesting name.", source="manual")
    await engine.drain()
    assert any(speech.status == "suppressed" for speech in engine.state.speeches)
    assert not any(message.get("type") == "speech.authorized" for message in published)
    await engine.stop()


@pytest.mark.asyncio
async def test_addressed_research_runs_speaker_and_worker_in_parallel() -> None:
    published: list[dict[str, object]] = []
    tools = ToolRegistry(1)

    async def fake_search(arguments: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "query": arguments["query"], "results": [{"title": "Source"}]}

    tools.register(
        ToolSpec(
            name="fake_search",
            description="Search test sources",
            input_schema={"query": "string"},
            effect="read",
            handler=fake_search,
        )
    )
    engine = build_engine(
        FakeDecision(route="investigate", addressed=True), ResearchGenerator(), published, tools=tools
    )
    await engine.start()
    await engine.start_session({})
    await engine.commit_utterance("Atlas, research the current situation.", source="manual")
    await engine.drain()
    agents = {run.agent for run in engine.state.agent_runs}
    assert {"speaker", "worker"}.issubset(agents)
    assert engine.state.tasks[-1].phase == "complete"
    assert engine.state.tasks[-1].status == "done"
    await engine.stop()


@pytest.mark.asyncio
async def test_proactive_semantic_investigation_runs_worker_without_assignment_ack() -> None:
    published: list[dict[str, object]] = []
    tools = ToolRegistry(1)

    async def fake_search(arguments: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "query": arguments["query"], "results": [{"title": "Evidence"}]}

    tools.register(
        ToolSpec(
            name="fake_search",
            description="Search test sources",
            input_schema={"query": "string"},
            effect="read",
            handler=fake_search,
        )
    )
    decision = SequenceDecision(
        [
            Decision(
                route="investigate",
                addressee="room",
                memory="capture",
                initiative="proactive",
                speech_depth="silent",
                timing="later",
            )
        ]
    )
    engine = build_engine(decision, ResearchGenerator(), published, tools=tools)
    await engine.start()
    await engine.start_session({})
    await engine.commit_utterance(
        "This assumption could invalidate the plan if the external facts differ.",
        source="manual",
    )
    await engine.drain()
    assert any(run.agent == "worker" for run in engine.state.agent_runs)
    assert engine.state.tasks[-1].status == "done"
    assert not any(speech.reason == "task_started" for speech in engine.state.speeches)
    await engine.stop()


@pytest.mark.asyncio
async def test_new_room_turn_cancels_stale_direct_speech() -> None:
    published: list[dict[str, object]] = []
    generator = BlockingGenerator()
    decision = SequenceDecision(
        [
            Decision(
                route="respond",
                addressee="atlas",
                memory="capture",
                speech_depth="normal",
                timing="next_gap",
            ),
            Decision(
                route="ignore",
                addressee="another_participant",
                speech_depth="silent",
                timing="silent",
            ),
        ]
    )
    engine = build_engine(decision, generator, published)
    await engine.start()
    await engine.start_session({})
    await engine.commit_utterance("Atlas, explain the situation.", source="manual")
    await asyncio.wait_for(generator.started.wait(), timeout=1)
    await engine.commit_utterance("No, I am talking to Pavel.", source="manual")
    await engine.drain()
    assert any(run.agent == "speaker" and run.status == "canceled" for run in engine.state.agent_runs)
    assert not any(message.get("type") == "speech.authorized" for message in published)
    await engine.stop()


@pytest.mark.asyncio
async def test_tts_failure_terminates_speech_and_releases_playback() -> None:
    published: list[dict[str, object]] = []
    engine = build_engine(
        FakeDecision(route="respond", addressed=True), FakeGenerator(), published, FailingTTS()
    )
    await engine.start()
    await engine.start_session({})
    await engine.commit_utterance("Atlas, give me the result.", source="manual")
    await engine.drain()
    speech = engine.state.speeches[-1]
    assert speech.status == "failed"
    assert speech.error and "provider stream closed" in speech.error
    assert engine.state.health["tts"].status == "down"
    assert engine._active_speech_id is None
    assert engine._playback_idle.is_set()
    assert any(message.get("type") == "speech.audio.end" for message in published)
    await engine.stop()


@pytest.mark.asyncio
async def test_requested_result_tts_failure_never_leaves_phantom_voice_active() -> None:
    published: list[dict[str, object]] = []
    engine = build_engine(
        FakeDecision(route="capture", addressed=False), FakeGenerator(), published, FailingTTS()
    )
    await engine.start()
    await engine.start_session({})
    speech = Speech(
        text="The research result is ready.",
        reason="requested_result",
        room_epoch=engine.state.room_epoch,
    )
    await engine._deliver(speech)
    assert speech.status == "failed"
    assert speech.error and "provider stream closed" in speech.error
    assert not any(message.get("type") == "speech.authorized" for message in published)
    assert engine._active_speech_id is None
    assert engine._playback_idle.is_set()
    await engine.stop()


@pytest.mark.asyncio
async def test_research_mission_fails_truthfully_when_no_tool_is_selected() -> None:
    published: list[dict[str, object]] = []
    engine = build_engine(FakeDecision(route="investigate", addressed=True), FakeGenerator(), published)
    await engine.start()
    await engine.start_session({})
    await engine.commit_utterance("Atlas, research the evidence.", source="manual")
    await engine.drain()
    mission = engine.state.tasks[-1]
    assert mission.status == "failed"
    assert mission.phase == "complete"
    assert mission.error == "No registered research tool was selected"
    await engine.stop()


@pytest.mark.asyncio
async def test_board_keeps_distinct_concepts_and_updates_matching_identity() -> None:
    published: list[dict[str, object]] = []
    engine = build_engine(FakeDecision(route="capture", addressed=False), FakeGenerator(), published)
    await engine.start()
    await engine.start_session({})
    await engine._apply_board_operations(
        [
            BoardOperation(
                action="create",
                concept_key="expert-validation-gap",
                kind="finding",
                title="Expert validation gap",
                body="Crisis workflows require domain expert review.",
            ),
            BoardOperation(
                action="create",
                concept_key="education-pivot",
                kind="idea",
                title="Education-domain pivot",
                body="The architecture may transfer to education coordination.",
            ),
        ],
        "utt_source",
    )
    await engine._apply_board_operations(
        [
            BoardOperation(
                action="create",
                concept_key="expert-validation-gap",
                kind="finding",
                title="Expert validation remains required",
                body="Experts must validate procedures and failure points.",
            )
        ],
        "utt_update",
    )
    assert len(engine.state.cards) == 2
    assert {card.concept_key for card in engine.state.cards} == {
        "expert-validation-gap",
        "education-pivot",
    }
    expert = next(card for card in engine.state.cards if card.concept_key == "expert-validation-gap")
    assert expert.title == "Expert validation remains required"
    assert expert.source_ids == ["utt_source", "utt_update"]
    await engine.stop()
