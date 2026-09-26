from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from sidecar.core.models import MeetingState, SessionSummary
from sidecar.languages import MeetingLanguage


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClientHello(WireModel):
    type: Literal["client.hello"]
    protocol_version: int
    client_id: str
    capabilities: dict[str, bool] = Field(default_factory=dict)


class SessionStart(WireModel):
    type: Literal["session.start"]
    assistant_name: str = "Assistant"
    language: MeetingLanguage = "en"
    capture_mode: Literal["microphone", "system", "mixed"] = "mixed"
    output_mode: Literal["local_only", "room_speaker", "meeting_injected"] = "local_only"


class SessionCommand(WireModel):
    type: Literal["session.command"]
    command: Literal["pause", "resume", "stop"]


class SessionOpen(WireModel):
    type: Literal["session.open"]
    session_id: str


class SessionLanguage(WireModel):
    type: Literal["session.language"]
    language: MeetingLanguage


class VoiceModeCommand(WireModel):
    type: Literal["voice.mode"]
    mode: Literal["active", "muted"]


class BoardCurate(WireModel):
    type: Literal["board.curate"]


class FloorChanged(WireModel):
    type: Literal["floor.changed"]
    busy: bool


class CaptureChanged(WireModel):
    type: Literal["capture.changed"]
    source: Literal["microphone", "system"]
    status: Literal["active", "denied", "ended", "unavailable"]


class TranscriptInject(WireModel):
    type: Literal["transcript.inject"]
    text: str


class PlaybackChanged(WireModel):
    type: Literal["playback.started", "playback.finished", "playback.interrupted"]
    speech_id: str


ClientMessage = Annotated[
    ClientHello
    | BoardCurate
    | SessionStart
    | SessionOpen
    | SessionLanguage
    | VoiceModeCommand
    | SessionCommand
    | FloorChanged
    | CaptureChanged
    | TranscriptInject
    | PlaybackChanged,
    Field(discriminator="type"),
]


class BootstrapResponse(WireModel):
    project_id: str
    protocol_version: int
    active_model: str
    models: list[str]
    state: MeetingState
    sessions: list[SessionSummary]


class SessionRenameRequest(WireModel):
    title: str = Field(min_length=1, max_length=80)


class BackendHello(WireModel):
    type: Literal["backend.hello"]
    project_id: str
    protocol_version: int


class StateSnapshot(WireModel):
    type: Literal["state.snapshot"]
    state: MeetingState
    sessions: list[SessionSummary]


class TranscriptPartial(WireModel):
    type: Literal["transcript.partial"]
    text: str


class SpeechAudio(WireModel):
    data_base64: str
    format: str
    sample_rate: int


class SpeechAuthorized(WireModel):
    type: Literal["speech.authorized"]
    speech_id: str
    text: str
    reason: str
    audio: SpeechAudio | None = None


class SpeechStop(WireModel):
    type: Literal["speech.stop"]
    speech_id: str
    reason: str


class PresenceCue(WireModel):
    type: Literal["presence.cue"]
    cue: Literal["thinking", "progress"]
    label: str
    audio: SpeechAudio | None = None


class ProtocolError(WireModel):
    type: Literal["protocol.error"]
    code: str
    message: str


ServerMessage = Annotated[
    BackendHello
    | StateSnapshot
    | TranscriptPartial
    | SpeechAuthorized
    | SpeechStop
    | PresenceCue
    | ProtocolError,
    Field(discriminator="type"),
]


class ProtocolDocument(WireModel):
    client: ClientMessage
    server: ServerMessage
