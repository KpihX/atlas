from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from sidecar.languages import MeetingLanguage


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class DomainModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProcessStatus(StrEnum):
    BOOTING = "booting"
    READY = "ready"
    SHUTTING_DOWN = "shutting_down"
    STOPPED = "stopped"
    FAILED = "failed"


class SessionStatus(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    LISTENING = "listening"
    PAUSED = "paused"
    FINALIZING = "finalizing"
    CLOSED = "closed"
    FAILED = "failed"


class Health(DomainModel):
    status: Literal["ok", "standby", "degraded", "down", "unconfigured"]
    detail: str = ""


class Utterance(DomainModel):
    id: str = Field(default_factory=lambda: new_id("utt"))
    text: str
    source: str = "audio"
    speaker: str = "unknown"
    language: str = "auto"
    confidence: float | None = None
    committed_at: str = Field(default_factory=now_iso)


class Card(DomainModel):
    id: str = Field(default_factory=lambda: new_id("card"))
    kind: Literal["idea", "question", "decision", "suggestion", "finding"] = "finding"
    title: str
    body: str
    source_ids: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=now_iso)


class Task(DomainModel):
    id: str = Field(default_factory=lambda: new_id("task"))
    tool: str
    summary: str
    status: Literal["queued", "running", "done", "failed", "canceled", "stale"] = "queued"
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = Field(default_factory=now_iso)
    completed_at: str | None = None


class Speech(DomainModel):
    id: str = Field(default_factory=lambda: new_id("say"))
    text: str
    reason: Literal["direct_address", "requested_result", "critical_finding"]
    status: Literal[
        "proposed", "waiting_gap", "authorized", "playing", "finished", "interrupted", "suppressed", "expired"
    ] = "proposed"
    source_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)


class Activity(DomainModel):
    id: str = Field(default_factory=lambda: new_id("act"))
    kind: str
    summary: str
    created_at: str = Field(default_factory=now_iso)


class AgentRun(DomainModel):
    id: str = Field(default_factory=lambda: new_id("agent"))
    agent: Literal["speaker", "worker", "coordinator", "notes", "naming"]
    summary: str
    status: Literal["running", "done", "failed"] = "running"
    error: str | None = None
    duration_ms: int | None = None
    started_at: str = Field(default_factory=now_iso)
    completed_at: str | None = None


class PipelineMetrics(DomainModel):
    audio_frames: int = 0
    audio_bytes: int = 0
    last_audio_at: str | None = None
    stt_messages: int = 0
    stt_fragments: int = 0
    stt_turns: int = 0
    stt_last_event: str = ""
    stt_last_fragment: str = ""
    stt_inactivity_probability: float = 0
    decisions: int = 0
    agent_runs: int = 0
    note_updates: int = 0


class Decision(DomainModel):
    route: Literal["ignore", "capture", "investigate", "respond", "act", "control"]
    addressed_probability: float = 0
    salience: float = 0
    speech_value: float = 0
    timing: Literal["silent", "next_gap", "later"] = "silent"
    rationale: str = ""


class DecisionRecord(DomainModel):
    id: str = Field(default_factory=lambda: new_id("decision"))
    utterance_id: str
    context: dict[str, Any]
    result: Decision
    decided_at: str = Field(default_factory=now_iso)


class MeetingState(DomainModel):
    project_id: str
    protocol_version: int
    process_status: ProcessStatus = ProcessStatus.BOOTING
    session_status: SessionStatus = SessionStatus.IDLE
    session_id: str | None = None
    title: str = "Nouvelle session"
    assistant_name: str = "Assistant"
    language: MeetingLanguage = "en"
    voice_mode: Literal["active", "muted"] = "active"
    capture_mode: Literal["microphone", "system", "mixed"] = "mixed"
    output_mode: Literal["local_only", "room_speaker", "meeting_injected"] = "local_only"
    floor_busy: bool = False
    partial: str = ""
    notes: str = ""
    notes_version: int = 0
    notes_cursor: int = 0
    title_version: int = 0
    title_cursor: int = 0
    working: str = ""
    transcript: list[Utterance] = []
    cards: list[Card] = []
    tasks: list[Task] = []
    speeches: list[Speech] = []
    activities: list[Activity] = []
    agent_runs: list[AgentRun] = []
    decisions: list[DecisionRecord] = []
    health: dict[str, Health] = {}
    pipeline: PipelineMetrics = Field(default_factory=PipelineMetrics)
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)


class SessionSummary(DomainModel):
    session_id: str
    title: str
    status: SessionStatus
    preview: str = ""
    utterance_count: int = 0
    created_at: str
    updated_at: str


class ToolRequest(DomainModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class BoardOperation(DomainModel):
    action: Literal["create", "update", "merge", "delete"]
    card_id: str | None = None
    merge_ids: list[str] = Field(default_factory=list)
    kind: Literal["idea", "question", "decision", "suggestion", "finding"] = "idea"
    title: str = ""
    body: str = ""
    reason: str = ""


class CoordinatorResult(DomainModel):
    working: str = ""
    board_ops: list[BoardOperation] = Field(default_factory=list)
    speech: str = ""
    control: Literal["none", "mute", "unmute", "end_session"] = "none"
    tool: ToolRequest | None = None


class AudioResult(DomainModel):
    data_base64: str
    format: str
    sample_rate: int


class LLMResult(DomainModel):
    content: str
    model: str
    provider: str
    raw: dict[str, Any] = Field(default_factory=dict)
