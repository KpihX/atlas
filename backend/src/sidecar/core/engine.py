from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from time import monotonic
from typing import Any, Literal

from sidecar.config import AppConfig, ProductConfig
from sidecar.languages import MEETING_LANGUAGES, MeetingLanguage

from .coordinator import Coordinator
from .models import (
    Activity,
    AgentRun,
    BoardOperation,
    Card,
    Decision,
    DecisionRecord,
    Health,
    MeetingState,
    ProcessStatus,
    SessionStatus,
    SessionSummary,
    Speech,
    Task,
    Utterance,
    new_id,
    now_iso,
)
from .notes import preserve_notes
from .ports import DecisionPort, Publish, StorePort, STTPort, TTSPort
from .speaker import SpeakerAgent
from .speech import SpeechGate
from .voice import spoken_text

logger = logging.getLogger("uvicorn.error").getChild("pipeline")


class MeetingEngine:
    def __init__(
        self,
        *,
        product: ProductConfig,
        config: AppConfig,
        store: StorePort,
        decision: DecisionPort,
        speaker: SpeakerAgent,
        coordinator: Coordinator,
        stt: STTPort,
        tts: TTSPort,
        publish: Publish,
        tool_health: dict[str, bool],
    ) -> None:
        self.product = product
        self.config = config
        self.store = store
        self.decision = decision
        self.speaker = speaker
        self.coordinator = coordinator
        self.stt = stt
        self.tts = tts
        self.publish = publish
        self.tool_health = tool_health
        self.state = self._new_state()
        self._gate = SpeechGate(config.policy)
        self._turn_lock = asyncio.Lock()
        self._notes_lock = asyncio.Lock()
        self._naming_lock = asyncio.Lock()
        self._maintenance_lock = asyncio.Lock()
        self._speech_lock = asyncio.Lock()
        self._playback_idle = asyncio.Event()
        self._playback_idle.set()
        self._active_speech_id: str | None = None
        self._presence_audio: dict[str, dict[str, object]] = {}
        self._notes_dirty = False
        self._notes_kick = asyncio.Event()
        self._notes_task: asyncio.Task[None] | None = None
        self._background: set[asyncio.Task[Any]] = set()
        self._speech_tasks: set[asyncio.Task[Any]] = set()
        self._last_floor_change = monotonic()
        self._stt_started_at = 0.0
        self._agent_started: dict[str, float] = {}

    async def start(self) -> None:
        await self.store.open()
        self.state.process_status = ProcessStatus.READY
        self._set_health()
        self._notes_task = asyncio.create_task(self._notes_loop())
        await self._commit("runtime", "Backend ready")

    async def stop(self) -> None:
        self.state.process_status = ProcessStatus.SHUTTING_DOWN
        await self._broadcast_state()
        if self._notes_task is not None:
            self._notes_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._notes_task
        for task in tuple(self._background):
            task.cancel()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        await self.stt.stop()
        close_decision = getattr(self.decision, "close", None)
        if close_decision is not None:
            await close_decision()
        close_generator = getattr(self.coordinator.generator, "close", None)
        if close_generator is not None:
            await close_generator()
        self.state.process_status = ProcessStatus.STOPPED
        await self.store.save(self.state)
        await self.store.close()

    async def start_session(self, payload: dict[str, Any]) -> None:
        await self._prepare_session_switch()
        self.state = self._new_state()
        self.state.process_status = ProcessStatus.READY
        self._set_health()
        self.state.session_status = SessionStatus.STARTING
        self.state.session_id = new_id("session")
        self.state.assistant_name = str(payload.get("assistant_name") or self.config.session.assistant_name)
        language = payload.get("language")
        self.state.language = language if language in MEETING_LANGUAGES else self.config.session.language
        capture = payload.get("capture_mode", "microphone")
        output = payload.get("output_mode", "local_only")
        self.state.capture_mode = capture if capture in {"microphone", "system", "mixed"} else "microphone"
        self.state.output_mode = (
            output if output in {"local_only", "room_speaker", "meeting_injected"} else "local_only"
        )
        await self._start_stt()
        self.state.session_status = SessionStatus.LISTENING
        await self._commit("session", "Listening started")

    async def open_session(self, session_id: str) -> bool:
        restored = await self.store.load(session_id)
        if restored is None:
            return False
        await self._prepare_session_switch()
        self.state = restored
        self.state.voice_mode = "active"
        for run in self.state.agent_runs:
            if run.status == "running":
                run.status = "failed"
                run.error = "process_restarted"
                run.completed_at = now_iso()
        for task in self.state.tasks:
            if task.status in {"queued", "running"}:
                task.status = "failed"
                task.error = "process_restarted"
                task.completed_at = now_iso()
        self.state.working = ""
        self.state.process_status = ProcessStatus.READY
        self.state.session_status = SessionStatus.PAUSED
        self._set_health()
        self._notes_dirty = len(self.state.transcript) > self.state.notes_cursor
        if self._notes_dirty:
            self._notes_kick.set()
        await self._commit("session", "Session opened")
        if self.state.title_version == 0 or len(self.state.transcript) - self.state.title_cursor >= 8:
            self._spawn(self._rename_session())
        return True

    async def list_sessions(self) -> list[SessionSummary]:
        return await self.store.list_sessions()

    def client_state(self) -> MeetingState:
        state = self.state.model_copy(deep=True)
        for task in state.tasks:
            if not isinstance(task.result, dict):
                continue
            results = task.result.get("results")
            if isinstance(results, list):
                sources = [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                    }
                    for item in results
                    if isinstance(item, dict)
                ]
                task.result = {
                    "status": task.result.get("status"),
                    "request_id": task.result.get("request_id"),
                    "result_count": len(results),
                    "sources": sources,
                }
        return state

    async def rename_session(self, session_id: str, title: str) -> bool:
        cleaned = " ".join(title.split()).strip()[:80]
        if not cleaned:
            return False
        state = self.state if self.state.session_id == session_id else await self.store.load(session_id)
        if state is None:
            return False
        state.title = cleaned
        state.updated_at = now_iso()
        await self.store.save(state)
        if self.state.session_id == session_id:
            await self._commit("session.renamed", cleaned)
        else:
            await self._broadcast_state()
        return True

    async def delete_session(self, session_id: str) -> bool:
        if self.state.session_id == session_id:
            await self._prepare_session_switch()
            deleted = await self.store.delete(session_id)
            self.state = self._new_state()
            self.state.process_status = ProcessStatus.READY
            self._set_health()
            await self._activity("session.deleted", session_id)
            await self._broadcast_state()
            return deleted
        deleted = await self.store.delete(session_id)
        if deleted:
            await self._broadcast_state()
        return deleted

    async def export_session(self, session_id: str) -> str | None:
        state = self.state if self.state.session_id == session_id else await self.store.load(session_id)
        if state is None:
            return None
        cards = "\n".join(f"### {card.title}\n\n{card.body}\n" for card in state.cards) or "_No cards._\n"
        transcript = (
            "\n".join(f"- [{item.id}] {item.speaker}: {item.text}" for item in state.transcript)
            or "_No transcript._"
        )
        tasks = (
            "\n".join(
                f"- [{item.status}] {item.tool}: {item.summary}"
                + (
                    "\n  "
                    + "\n  ".join(
                        f"- {result.get('title', 'Source')}: {result.get('url', '')}"
                        for result in item.result.get("results", [])
                        if isinstance(result, dict)
                    )
                    if isinstance(item.result, dict) and isinstance(item.result.get("results"), list)
                    else ""
                )
                for item in state.tasks
            )
            or "_No tool runs._"
        )
        agents = (
            "\n".join(f"- [{item.status}] {item.agent}: {item.summary}" for item in state.agent_runs)
            or "_No agent runs._"
        )
        return (
            f"# {state.title}\n\n"
            f"Created: {state.created_at}\n\n"
            f"## Live Notes\n\n{state.notes or '_No notes._'}\n\n"
            f"## Board\n\n{cards}\n"
            f"## Agents\n\n{agents}\n\n"
            f"## Research and Tools\n\n{tasks}\n\n"
            f"## Transcript\n\n{transcript}\n"
        )

    async def curate_board(self) -> None:
        if not self.state.cards:
            return
        async with self._maintenance_lock:
            session_id = self.state.session_id
            snapshot = self.state.model_copy(deep=True)
            agent_run = await self._start_agent("coordinator", "Consolidating the board")
            try:
                operations = await self.coordinator.curate_board(snapshot)
                if self.state.session_id == session_id:
                    async with self._turn_lock:
                        await self._apply_board_operations(operations, "")
                        await self._finish_agent(agent_run, "done")
                        await self._commit("board.curated", f"Applied {len(operations)} board operations")
                else:
                    await self._finish_agent(agent_run, "failed", "session_changed")
            except Exception as error:
                await self._finish_agent(agent_run, "failed", type(error).__name__)
                await self._commit("error", f"Board curation failed: {type(error).__name__}")

    async def session_command(self, command: str) -> None:
        if command == "pause" and self.state.session_status == SessionStatus.LISTENING:
            await self._cancel_speech_tasks()
            await self._interrupt_playback("session_paused")
            self.state.session_status = SessionStatus.PAUSED
            await self.stt.stop()
            self.state.health["stt"] = Health(status="standby", detail="Paused with the session")
            await self._commit("session", "Listening paused")
        elif command == "resume" and self.state.session_status == SessionStatus.PAUSED:
            await self._start_stt()
            self.state.session_status = SessionStatus.LISTENING
            await self._commit("session", "Listening resumed")
        elif command == "stop" and self.state.session_status not in {
            SessionStatus.IDLE,
            SessionStatus.CLOSED,
        }:
            await self._cancel_speech_tasks()
            await self._interrupt_playback("session_ended")
            self.state.session_status = SessionStatus.FINALIZING
            await self.stt.stop()
            if self._notes_dirty:
                await self._write_notes()
            await self._rename_session(force=True)
            self.state.session_status = SessionStatus.CLOSED
            await self._commit("session", "Session closed")

    async def set_language(self, language: MeetingLanguage) -> None:
        if language == self.state.language:
            return
        listening = self.state.session_status == SessionStatus.LISTENING
        if listening:
            await self.stt.stop()
        self.state.language = language
        self.state.partial = ""
        if listening:
            await self._start_stt()
        await self._commit("session.language", language)

    async def set_voice_mode(self, mode: Literal["active", "muted"]) -> None:
        self.state.voice_mode = mode
        await self._commit(f"voice.{mode}", f"Voice mode is {mode}")

    async def client_disconnected(self) -> None:
        await self._interrupt_playback("client_disconnected")
        if self.state.session_status == SessionStatus.LISTENING:
            self.state.session_status = SessionStatus.PAUSED
            await self.stt.stop()
            self.state.health["stt"] = Health(status="standby", detail="Capture client disconnected")
            await self._commit("session", "Capture client disconnected; session paused")

    async def ingest_audio(self, audio: bytes) -> None:
        if self.state.session_status == SessionStatus.LISTENING:
            if (
                self.stt.connected
                and monotonic() - self._stt_started_at >= self.config.voice.stt.rotate_after_seconds
            ):
                await self.stt.stop()
            if self.stt.available and not self.stt.connected:
                await self._start_stt()
            self.state.pipeline.audio_frames += 1
            self.state.pipeline.audio_bytes += len(audio)
            self.state.pipeline.last_audio_at = now_iso()
            try:
                await self.stt.send(audio)
            except Exception as error:
                logger.warning("stt.reconnect error=%s", type(error).__name__)
                self.state.health["stt"] = Health(status="degraded", detail="Reconnecting live stream")
                await self.stt.stop()
                await self._start_stt()
                await self.stt.send(audio)
            if self.state.pipeline.audio_frames % 12 == 0:
                logger.info(
                    "audio.received frames=%d bytes=%d",
                    self.state.pipeline.audio_frames,
                    self.state.pipeline.audio_bytes,
                )
                await self._broadcast_state()

    async def on_partial(self, text: str) -> None:
        self.state.partial = text
        await self.publish({"type": "transcript.partial", "text": text})

    async def on_stt_event(self, kind: str, detail: dict[str, object]) -> None:
        self.state.pipeline.stt_messages += 1
        self.state.pipeline.stt_last_event = kind
        if kind == "text":
            self.state.pipeline.stt_fragments += 1
            self.state.pipeline.stt_last_fragment = str(detail.get("text", ""))
        probability = detail.get("inactivity_probability")
        if isinstance(probability, int | float):
            self.state.pipeline.stt_inactivity_probability = float(probability)
        if kind == "error":
            self.state.health["stt"] = Health(status="down", detail=str(detail)[:180])
            await self._broadcast_state()

    async def commit_utterance(self, text: str, source: str = "audio") -> None:
        cleaned = " ".join(text.split()).strip()
        if not cleaned or self.state.session_status != SessionStatus.LISTENING:
            return
        utterance = Utterance(text=cleaned, source=source, language=self.state.language)
        self.state.partial = ""
        self.state.transcript.append(utterance)
        if source == "audio":
            self.state.pipeline.stt_turns += 1
        self.state.transcript = self.state.transcript[-500:]
        self._notes_dirty = True
        self._notes_kick.set()
        logger.info("transcript.committed source=%s text=%r", source, cleaned)
        await self._commit("heard", cleaned)
        if (
            self.state.title == "Nouvelle session"
            or len(self.state.transcript) - self.state.title_cursor >= 8
        ):
            self._spawn(self._rename_session())
        self._spawn(self._process_turn(utterance))

    async def floor_changed(self, busy: bool) -> None:
        if self.state.floor_busy == busy:
            return
        self.state.floor_busy = busy
        self._last_floor_change = monotonic()
        if busy:
            for speech in reversed(self.state.speeches):
                if speech.status in {"authorized", "playing"}:
                    speech.status = "interrupted"
                    self._active_speech_id = None
                    self._playback_idle.set()
                    await self.publish({"type": "speech.stop", "speech_id": speech.id, "reason": "barge_in"})
                    break
        else:
            await self.stt.flush()
        await self._broadcast_state()

    async def playback_changed(self, speech_id: str, status: str) -> None:
        speech = next((item for item in self.state.speeches if item.id == speech_id), None)
        if speech is None or status not in {"playing", "finished", "interrupted"}:
            return
        speech.status = status  # type: ignore[assignment]
        if status in {"finished", "interrupted"} and self._active_speech_id == speech_id:
            self._active_speech_id = None
            self._playback_idle.set()
        await self._commit("playback", f"{status}: {speech.text}")

    async def _interrupt_playback(self, reason: str) -> None:
        speech_id = self._active_speech_id
        if speech_id is None:
            return
        speech = next((item for item in self.state.speeches if item.id == speech_id), None)
        if speech is not None:
            speech.status = "interrupted"
        self._active_speech_id = None
        self._playback_idle.set()
        await self.publish({"type": "speech.stop", "speech_id": speech_id, "reason": reason})

    async def drain(self) -> None:
        while self._background:
            await asyncio.gather(*tuple(self._background))

    async def _process_turn(self, utterance: Utterance) -> None:
        session_id = self.state.session_id
        decision_state = self.state.model_copy(deep=True)
        try:
            decision = await self.decision.evaluate(decision_state, utterance.text)
        except Exception as error:
            await self._commit("error", f"Decision failed: {type(error).__name__}")
            return
        if self.state.session_id != session_id or self.state.session_status == SessionStatus.CLOSED:
            return
        self.state.pipeline.decisions += 1
        self.state.decisions.append(
            DecisionRecord(
                utterance_id=utterance.id,
                context={
                    "assistant_name": decision_state.assistant_name,
                    "new_utterance": utterance.text,
                    "recent_transcript": [item.text for item in decision_state.transcript[-8:]],
                    "current_notes": decision_state.notes,
                    "running_tasks": [
                        item.summary for item in decision_state.tasks if item.status == "running"
                    ],
                },
                result=decision,
            )
        )
        self.state.decisions = self.state.decisions[-100:]
        logger.info(
            "decision route=%s addressed=%.2f timing=%s",
            decision.route,
            decision.addressed_probability,
            decision.timing,
        )
        self.state.health["decision"] = Health(
            status="ok" if decision.rationale.startswith("Jev") else "degraded",
            detail=decision.rationale,
        )
        await self._activity("decided", f"{decision.route}: {decision.rationale}")
        await self._broadcast_state()

        wake_name = self.state.assistant_name.casefold().strip()
        has_wake = bool(wake_name and wake_name in utterance.text.casefold())
        addressed_strictly = decision.addressed_probability >= 0.7 or has_wake

        should_speak = (addressed_strictly and decision.route in {"respond", "control"}) or (
            decision.speech_value >= 0.9 and decision.route == "respond"
        )
        if should_speak:
            self._spawn(self._speaker_turn(utterance, decision, session_id))

        if decision.route in {"ignore", "respond", "control"}:
            return

        async with self._turn_lock:
            agent_run: AgentRun | None = None
            try:
                if self.state.session_id != session_id or self.state.session_status == SessionStatus.CLOSED:
                    return
                agent_run = await self._start_agent("worker", f"Processing: {utterance.text[:80]}")
                result = await self.coordinator.run(self.state, decision, on_task=self._upsert_task)
                if self.state.session_id != session_id or self.state.session_status == SessionStatus.CLOSED:
                    if agent_run is not None:
                        await self._finish_agent(agent_run, "failed", "session_closed")
                    return
                self.state.pipeline.agent_runs += 1
                logger.info(
                    "agent.completed working=%r board_ops=%d speech=%s",
                    result.working,
                    len(result.board_ops),
                    bool(result.speech),
                )
                if self.coordinator.generator.available:
                    self.state.health["generator"] = Health(status="ok")
                self.state.working = result.working
                await self._apply_board_operations(result.board_ops, utterance.id)
                await self._finish_agent(agent_run, "done")
                await self._commit("worker", result.working or "Turn completed")
            except Exception as error:
                self.state.working = ""
                if agent_run is not None:
                    await self._finish_agent(agent_run, "failed", type(error).__name__)
                await self._commit("error", f"Worker failed: {type(error).__name__}")

    async def _speaker_turn(self, utterance: Utterance, decision: Decision, session_id: str | None) -> None:
        run = await self._start_agent("speaker", f"Responding: {utterance.text[:80]}")
        try:
            result = await self.speaker.answer(self.state.model_copy(deep=True), utterance, decision)
            if self.state.session_id != session_id or self.state.session_status == SessionStatus.CLOSED:
                await self._finish_agent(run, "failed", "session_closed")
                return
            await self._finish_agent(run, "done")
            if await self._apply_control(result.control):
                return
            await self._offer_speaker_text(result.speech, "direct_address", [utterance.id])
        except Exception as error:
            await self._finish_agent(run, "failed", type(error).__name__)
            await self._commit("error", f"Speaker failed: {type(error).__name__}")

    async def _speaker_task(self, task: Task, session_id: str | None) -> None:
        run = await self._start_agent("speaker", f"Reporting: {task.summary[:80]}")
        try:
            result = await self.speaker.report_task(self.state.model_copy(deep=True), task)
            if self.state.session_id != session_id or self.state.session_status == SessionStatus.CLOSED:
                await self._finish_agent(run, "failed", "session_closed")
                return
            await self._finish_agent(run, "done")
            await self._offer_speaker_text(result.speech, "requested_result", [task.id])
        except Exception as error:
            await self._finish_agent(run, "failed", type(error).__name__)
            await self._commit("error", f"Speaker progress failed: {type(error).__name__}")

    async def _offer_speaker_text(
        self,
        text: str,
        reason: Literal["direct_address", "requested_result", "critical_finding"],
        source_ids: list[str],
    ) -> None:
        natural_speech = spoken_text(text)
        if not natural_speech:
            return
        self._spawn(
            self._deliver(Speech(text=natural_speech, reason=reason, source_ids=source_ids)),
            speech=True,
        )

    async def _apply_control(self, control: str) -> bool:
        if control == "mute":
            self.state.voice_mode = "muted"
            await self._commit("voice.muted", "Assistant keeps listening silently")
        elif control == "unmute":
            self.state.voice_mode = "active"
            await self._commit("voice.active", "Assistant may speak again")
        elif control == "end_session":
            await self.session_command("stop")
            return True
        return False

    async def _upsert_task(self, task: Task) -> None:
        current = next((index for index, item in enumerate(self.state.tasks) if item.id == task.id), None)
        if current is None:
            self.state.tasks.append(task)
        else:
            self.state.tasks[current] = task
        self.state.tasks = self.state.tasks[-100:]
        logger.info("tool.%s name=%s summary=%r", task.status, task.tool, task.summary)
        provider = "exa" if task.tool == "exa_search" else "jinko" if task.tool.startswith("jinko_") else None
        if provider is not None and task.status in {"done", "failed"}:
            self.state.health[provider] = Health(
                status="ok" if task.status == "done" else "down",
                detail=task.error or "",
            )
        await self._commit("task", f"{task.status}: {task.summary}")
        if task.status in {"done", "failed"}:
            self._spawn(self._speaker_task(task.model_copy(deep=True), self.state.session_id))

    async def _apply_board_operations(self, operations: list[BoardOperation], source_id: str) -> None:
        for operation in operations:
            if operation.action == "create":
                if not operation.title or not operation.body:
                    continue
                card = Card(
                    kind=operation.kind,
                    title=operation.title,
                    body=operation.body,
                    source_ids=[source_id],
                )
                self.state.cards.append(card)
                await self._activity("board.create", f"{card.title}: {operation.reason}")
                continue
            target = next((card for card in self.state.cards if card.id == operation.card_id), None)
            if target is None:
                continue
            if operation.action in {"update", "merge"}:
                target.kind = operation.kind
                target.title = operation.title or target.title
                target.body = operation.body or target.body
                target.source_ids = list(
                    dict.fromkeys([*target.source_ids, *([source_id] if source_id else [])])
                )
                target.updated_at = now_iso()
            if operation.action == "merge":
                merged = [card for card in self.state.cards if card.id in operation.merge_ids]
                for card in merged:
                    target.source_ids = list(dict.fromkeys([*target.source_ids, *card.source_ids]))
                removed = set(operation.merge_ids) - {target.id}
                self.state.cards = [card for card in self.state.cards if card.id not in removed]
            elif operation.action == "delete":
                self.state.cards = [card for card in self.state.cards if card.id != target.id]
            await self._activity(f"board.{operation.action}", f"{target.title}: {operation.reason}")
        self.state.cards = self.state.cards[-100:]

    async def _deliver(self, speech: Speech) -> None:
        async with self._speech_lock:
            blocked = self._gate.validate(speech)
            if self.state.voice_mode == "muted":
                blocked = "voice_muted"
            self.state.speeches.append(speech)
            self.state.speeches = self.state.speeches[-100:]
            if blocked:
                speech.status = "suppressed"
                await self._commit("suppressed", f"{blocked}: {speech.text}")
                return
            try:
                await asyncio.wait_for(self._playback_idle.wait(), timeout=45)
            except TimeoutError:
                if self._active_speech_id is not None:
                    await self.publish(
                        {
                            "type": "speech.stop",
                            "speech_id": self._active_speech_id,
                            "reason": "playback_timeout",
                        }
                    )
                self._active_speech_id = None
                self._playback_idle.set()
            speech.status = "waiting_gap"
            await self._broadcast_state()
            tts_task: asyncio.Task[Any] | None = None
            if self.tts.available:
                tts_task = asyncio.create_task(self.tts.synthesize(speech.text, self.state.language))
                self._background.add(tts_task)
                self._speech_tasks.add(tts_task)
                tts_task.add_done_callback(self._background.discard)
                tts_task.add_done_callback(self._speech_tasks.discard)
            started = monotonic()
            required_gap = (
                self.config.policy.direct_floor_gap_seconds
                if speech.reason == "direct_address"
                else self.config.policy.stable_floor_gap_seconds
            )
            force_after = (
                self.config.policy.direct_force_after_seconds
                if speech.reason == "direct_address"
                else self.config.policy.proactive_force_after_seconds
            )
            while True:
                quiet_for = monotonic() - self._last_floor_change
                if not self.state.floor_busy and quiet_for >= required_gap:
                    break
                if monotonic() - started >= force_after:
                    await self._activity("speech.floor_claimed", speech.text[:120])
                    break
                await asyncio.sleep(0.05)
            audio: dict[str, object] | None = None
            if tts_task is not None:
                try:
                    audio = self.tts_result(await tts_task)
                    self.state.health["tts"] = Health(status="ok")
                except Exception as error:
                    self.state.health["tts"] = Health(status="down", detail=type(error).__name__)
            speech.status = "authorized"
            self._active_speech_id = speech.id
            self._playback_idle.clear()
            self._gate.delivered(speech)
            await self.store.save(self.state)
            await self.publish(
                {
                    "type": "speech.authorized",
                    "speech_id": speech.id,
                    "text": speech.text,
                    "reason": speech.reason,
                    "audio": audio,
                }
            )
            await self._broadcast_state()

    @staticmethod
    def tts_result(result: Any) -> dict[str, object]:
        return {
            "data_base64": result.data_base64,
            "format": result.format,
            "sample_rate": result.sample_rate,
        }

    async def _notes_loop(self) -> None:
        while True:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self._notes_kick.wait(), timeout=self.config.session.notes_interval_seconds
                )
            self._notes_kick.clear()
            await asyncio.sleep(1.5)
            if self._notes_dirty:
                await self._write_notes()

    async def _write_notes(self) -> None:
        async with self._notes_lock:
            if not self._notes_dirty:
                return
            cursor = len(self.state.transcript)
            session_id = self.state.session_id
            snapshot = self.state.model_copy(deep=True)
            agent_run = await self._start_agent("notes", f"Updating notes v{self.state.notes_version + 1}")
            try:
                notes = await self.coordinator.write_notes(snapshot)
                if notes and self.state.session_id == session_id:
                    notes = preserve_notes(
                        snapshot.notes,
                        notes,
                        snapshot.transcript[snapshot.notes_cursor :],
                    )
                    self.state.notes = notes
                    self.state.notes_cursor = cursor
                    self.state.notes_version += 1
                    self.state.pipeline.note_updates += 1
                    logger.info(
                        "notes.updated version=%d characters=%d", self.state.notes_version, len(notes)
                    )
                    self._notes_dirty = len(self.state.transcript) > cursor
                    await self._finish_agent(agent_run, "done")
                    await self._commit("notes", f"Live notes v{self.state.notes_version} updated")
                else:
                    await self._finish_agent(agent_run, "done")
            except Exception as error:
                await self._finish_agent(agent_run, "failed", type(error).__name__)
                await self._activity("error", f"Notes failed: {type(error).__name__}")
                await self._broadcast_state()

    async def _rename_session(self, *, force: bool = False) -> None:
        async with self._naming_lock:
            session_id = self.state.session_id
            if session_id is None or not self.state.transcript:
                return
            cursor = len(self.state.transcript)
            if cursor <= self.state.title_cursor and not force:
                return
            snapshot = self.state.model_copy(deep=True)
            agent_run = await self._start_agent("naming", "Revising the session title")
            try:
                title = await self.coordinator.name_session(snapshot)
                if self.state.session_id == session_id:
                    self.state.title_cursor = cursor
                    self.state.title_version += 1
                    if title != self.state.title:
                        self.state.title = title
                        await self._commit("session.renamed", title)
                await self._finish_agent(agent_run, "done")
            except Exception as error:
                await self._finish_agent(agent_run, "failed", type(error).__name__)
                await self._activity("error", f"Session naming failed: {type(error).__name__}")

    def _set_health(self) -> None:
        self.state.health = {
            "storage": Health(status="ok", detail=str(self.config.storage.resolved_path())),
            "stt": Health(status="standby" if self.stt.available else "unconfigured"),
            "tts": Health(status="standby" if self.tts.available else "unconfigured"),
            "decision": Health(
                status="standby" if getattr(self.decision, "available", False) else "degraded"
            ),
            "generator": Health(status="standby" if self.coordinator.generator.available else "unconfigured"),
            **{
                name: Health(status="standby" if available else "unconfigured")
                for name, available in self.tool_health.items()
            },
        }

    async def _start_stt(self) -> None:
        if not self.stt.available:
            self.state.health["stt"] = Health(status="unconfigured")
            return
        try:
            await self.stt.start(
                self.on_partial,
                self.commit_utterance,
                self.on_stt_event,
                self.state.language,
            )
            self._stt_started_at = monotonic()
            self.state.health["stt"] = Health(status="ok", detail="Live Gradium stream")
        except Exception as error:
            self.state.health["stt"] = Health(
                status="down", detail=f"{type(error).__name__}: {str(error)[:180]}"
            )

    async def _activity(self, kind: str, summary: str) -> None:
        self.state.activities.append(Activity(kind=kind, summary=summary))
        self.state.activities = self.state.activities[-200:]

    async def _start_agent(self, agent: str, summary: str) -> AgentRun:
        run = AgentRun(agent=agent, summary=summary)  # type: ignore[arg-type]
        self.state.agent_runs.append(run)
        self._agent_started[run.id] = monotonic()
        self.state.agent_runs = self.state.agent_runs[-100:]
        self.state.working = summary
        await self._activity("agent.started", f"{agent}: {summary}")
        await self.store.save(self.state)
        await self._broadcast_state()
        if agent == "speaker" and not self.state.floor_busy and self.state.voice_mode == "active":
            self._spawn(self._publish_presence_cue(run.id, summary), speech=True)
        return run

    async def _publish_presence_cue(self, run_id: str, label: str) -> None:
        if not self.tts.available:
            return
        language = self.state.language
        audio = self._presence_audio.get(language)
        if audio is None:
            cue_text = "Mmh..." if language == "fr" else "Mmm..."
            try:
                result = await self.tts.synthesize(cue_text, language)
                audio = self.tts_result(result)
                self._presence_audio[language] = audio
            except Exception as error:
                logger.warning("presence.cue synthesize failed: %s", type(error).__name__)
                return
        run = next((item for item in self.state.agent_runs if item.id == run_id), None)
        if run is None or run.status != "running" or self.state.floor_busy:
            return
        await self.publish(
            {
                "type": "presence.cue",
                "cue": "thinking",
                "label": label,
                "audio": audio,
            }
        )

    async def _finish_agent(self, run: AgentRun, status: str, error: str | None = None) -> None:
        if run.status != "running":
            return
        run.status = status  # type: ignore[assignment]
        run.error = error
        started = self._agent_started.pop(run.id, None)
        run.duration_ms = round((monotonic() - started) * 1000) if started is not None else None
        run.completed_at = now_iso()
        if not any(item.status == "running" for item in self.state.agent_runs):
            self.state.working = ""
        await self._activity(f"agent.{status}", f"{run.agent}: {run.summary}")
        await self.store.save(self.state)
        await self._broadcast_state()

    async def _commit(self, kind: str, summary: str) -> None:
        await self._activity(kind, summary)
        self.state.updated_at = now_iso()
        await self.store.save(self.state)
        await self._broadcast_state()

    async def _broadcast_state(self) -> None:
        await self.publish(
            {
                "type": "state.snapshot",
                "state": self.client_state().model_dump(mode="json"),
                "sessions": [item.model_dump(mode="json") for item in await self.store.list_sessions()],
            }
        )

    async def _prepare_session_switch(self) -> None:
        await self._cancel_speech_tasks()
        await self._interrupt_playback("session_changed")
        if self.state.session_status in {SessionStatus.STARTING, SessionStatus.LISTENING}:
            await self.stt.stop()
            self.state.session_status = SessionStatus.PAUSED
            self.state.updated_at = now_iso()
            await self.store.save(self.state)
        for task in tuple(self._background):
            task.cancel()
        if self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
        self._background.clear()
        self._notes_dirty = False
        self._notes_kick.clear()

    def _new_state(self) -> MeetingState:
        return MeetingState(
            project_id=self.product.project_id,
            protocol_version=self.product.protocol_version,
            assistant_name=self.config.session.assistant_name,
            language=self.config.session.language,
        )

    async def _cancel_speech_tasks(self) -> None:
        for speech in self.state.speeches:
            if speech.status in {"proposed", "waiting_gap"}:
                speech.status = "interrupted"
        current = asyncio.current_task()
        tasks = [task for task in self._speech_tasks if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._speech_tasks.difference_update(tasks)

    def _spawn(self, awaitable: Any, *, speech: bool = False) -> None:
        task = asyncio.create_task(awaitable)
        self._background.add(task)
        if speech:
            self._speech_tasks.add(task)
        task.add_done_callback(self._background.discard)
        task.add_done_callback(self._speech_tasks.discard)
