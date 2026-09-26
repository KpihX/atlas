from __future__ import annotations

import base64
import json
from typing import Any

import pytest

from sidecar.config import AppConfig, ProductConfig, default_config_text
from sidecar.core.coordinator import Coordinator
from sidecar.core.engine import MeetingEngine
from sidecar.core.models import (
    AudioResult,
    BoardOperation,
    Card,
    Decision,
    LLMResult,
    MeetingState,
    ProcessStatus,
    SessionStatus,
    SessionSummary,
)
from sidecar.core.speaker import SpeakerAgent
from sidecar.core.tools import ToolRegistry


class MemoryStore:
    states: dict[str, MeetingState]

    def __init__(self) -> None:
        self.states = {}

    async def open(self) -> None:
        pass

    async def load(self, session_id: str) -> MeetingState | None:
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

    async def save(self, state: MeetingState) -> None:
        if state.session_id:
            self.states[state.session_id] = state.model_copy(deep=True)

    async def delete(self, session_id: str) -> bool:
        return self.states.pop(session_id, None) is not None

    async def close(self) -> None:
        pass


class FakeDecision:
    available = True

    async def evaluate(self, state: MeetingState, text: str) -> Decision:
        return Decision(route="respond", addressed_probability=1, speech_value=2, timing="next_gap")


class FakeGenerator:
    available = True

    async def generate(self, messages: list[dict[str, str]], model_id: str | None = None) -> LLMResult:
        if messages[0]["content"].startswith("Give this evolving meeting"):
            return LLMResult(content="Answer Review", model="fake", provider="fake")
        if messages[0]["content"].startswith("You maintain one living Markdown"):
            return LLMResult(content="# Answer Review\n\n- Living notes", model="fake", provider="fake")
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

    async def synthesize(self, text: str, language: str) -> AudioResult:
        return AudioResult(
            data_base64=base64.b64encode(b"\0\0").decode(), format="pcm_24000", sample_rate=24000
        )


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


def test_coordinator_accepts_zen_yaml_shape() -> None:
    state = MeetingState(project_id="test", protocol_version=1)
    decision = Decision(route="respond", addressed_probability=1)
    result = Coordinator._parse(
        "speech: Bonjour\ncard_title: Test\ncard_body: OK\ncard_kind: finding\nworking: Done\ntool: null",
        state,
        decision,
    )
    assert result.speech == "Bonjour"
    assert result.board_ops[0].title == "Test"


def test_coordinator_ignores_empty_tool_name() -> None:
    state = MeetingState(project_id="test", protocol_version=1)
    decision = Decision(route="respond", addressed_probability=1)
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
    coordinator = Coordinator(generator, ToolRegistry(1), config.policy)
    engine = MeetingEngine(
        product=ProductConfig(project_id="test", protocol_version=1),
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
    await engine.start_session({"assistant_name": "Assistant"})
    await engine.commit_utterance("Assistant, what is the answer?", source="manual")
    await engine.drain()
    assert engine.state.title == "Answer Review"
    assert any(message.get("type") == "speech.authorized" for message in published)
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
    engine = MeetingEngine(
        product=ProductConfig(project_id="test", protocol_version=1),
        config=config,
        store=store,
        decision=FakeDecision(),
        speaker=SpeakerAgent(FakeGenerator()),
        coordinator=Coordinator(FakeGenerator(), ToolRegistry(1), config.policy),
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
    engine = MeetingEngine(
        product=ProductConfig(project_id="test", protocol_version=1),
        config=config,
        store=MemoryStore(),
        decision=FakeDecision(),
        speaker=SpeakerAgent(FakeGenerator()),
        coordinator=Coordinator(FakeGenerator(), ToolRegistry(1), config.policy),
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
