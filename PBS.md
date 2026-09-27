# Atlas Problem Book

This document records observed product failures before the Atlas refactor. It is grounded in the five latest persisted sessions read in full from SQLite on 2026-09-27. It is not a wishlist. Each item must have a regression test before closure.

## Evidence set

| Session | Turns | Notes revisions | Cards | Tasks | Speeches | Agent runs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Nepal Flood Response Assistant Hackathon Planning | 28 | 19 | 2 | 2 | 7 | 56 |
| German Hackathons and French Equivalents | 11 | 10 | 3 | 3 | 9 | 26 |
| Presentation assistant collaborator reunion | 1 | 1 | 0 | 0 | 1 | 3 |
| Presentation assistant and meeting capabilities | 1 | 1 | 0 | 0 | 1 | 3 |
| Presenting Assistant Gene Code Research Tools | 23 | 20 | 2 | 3 | 8 | 47 |

## P0: Conversation and truth failures

### PBS-001: Atlas answers another participant's turn

Evidence:

- `Hey Pavel...` was routed to `respond` with address probability 0.24.
- `Avery, do you see what assistant just said?` was routed to `respond` with address probability 0.23.
- `No, Pavel, Pavel, I'm talking to you` was routed to `respond` with address probability 0.16.
- Atlas then generated spoken replies for all three turns.

Root cause:

- Jev's route can force the Speaker even when addressivity says Atlas is not the addressee.
- The Speaker has no independent semantic veto.

Acceptance:

- Jev estimates continuous addressivity using utterance, participants, recent turns, prosody, room state and agent state.
- Speaker independently returns `speak: false` when Atlas is not addressed or has nothing useful to say.
- Regression fixtures cover explicit human names, group questions, indirect invitations and ambiguous references.

### PBS-002: Stop-speaking command still produces speech

Evidence:

- `stop stop assistant stop talking, I'm talking to Pavel` produced a finished spoken reply.

Root cause:

- Control handling and response generation are not mutually exclusive.

Acceptance:

- A mute/stop control cancels queued speech, current synthesis and playback immediately.
- The control itself does not produce a spoken response unless explicitly requested.

### PBS-003: Atlas lies about tools and work state

Evidence:

- After three successful Exa tasks, Atlas said it had no research tool, no reservation tool and was only a meeting voice.
- Notes said research was pending after task completion.
- Status replies said work was still running when task state was empty.

Root cause:

- Speaker receives an incomplete projection of shared memory.
- Tool capability and task truth are not derived from the canonical registry and task state.

Acceptance:

- Speaker receives exact capabilities, active tasks, completed results and failures.
- `system_capabilities` is generated from the live tool registry.
- Atlas never claims a capability absent from the registry and never denies a capability present in it.

### PBS-004: Research result exists but users wait and relaunch it

Evidence:

- Exa searches completed in roughly one to two seconds.
- Users repeatedly said they were still waiting.
- France research was relaunched multiple times and produced duplicate task/result cycles.

Root cause:

- Task completion, result integration, Notes and spoken delivery are independent but not synchronized through one lifecycle.
- No idempotent mission identity prevents semantically duplicate work.

Acceptance:

- A mission has one stable ID from request through acknowledgement, tool execution, result integration and spoken delivery.
- Equivalent active/completed missions are reused instead of duplicated.
- Atlas acknowledges work immediately, reports meaningful progress and presents the final result once.

### PBS-005: Capture turns launch fake Workers

Evidence:

- Fragments such as `I don't know`, `maybe we could`, and `of what you are doing` launched Worker runs lasting 6 to 20 seconds.
- A 28-turn session produced 56 agent runs.

Root cause:

- The engine starts a Worker for normal capture/board curation instead of restricting Workers to actual missions.

Acceptance:

- Capture updates memory only.
- A Worker exists only for a concrete mission with visible lifecycle and output.
- Flow UI never marks Workers active for passive listening or ordinary board maintenance.

## P0: Voice and realtime failures

### PBS-006: First audible response is too slow

Evidence:

- Muse Speaker responses took roughly 9 to 21 seconds.
- Buffered TTS added more than two seconds.
- Current measured target path is DeepSeek v4 Flash plus streamed Gradium PCM.

Acceptance:

- Speaker model and TTS roles are independently configurable as `provider/model`.
- First audible response latency is measured end to end.
- Target: median under 1.2 seconds on the current environment, with transparent metrics.

### PBS-007: Voice output can read structured artifacts

Evidence:

- Users heard tool/JSON-like symbols and machine structure.

Acceptance:

- Spoken output has a dedicated validated schema and plain-language field.
- URLs, IDs, JSON, Markdown markers and tool traces cannot enter TTS.
- Spoken rendering is tested against adversarial structured payloads.

### PBS-008: TTS/session failures are hidden

Evidence:

- Latest session persisted `tts: down / RuntimeError` while speeches remained recorded.
- Gradium STT connections expired at 300 seconds and previously crashed the browser socket.

Acceptance:

- STT rotates before provider limits and reconnects without dropping the product session.
- TTS failure changes visible state and does not mark speech as successfully played.
- Voice transport has explicit ready, streaming, interrupted, done and failed states.

### PBS-009: Speech is too long and repeats research dumps

Evidence:

- Several spoken outputs contain hundreds of words and repeat the same research in multiple forms.

Acceptance:

- Atlas gives a concise spoken synthesis first.
- Detail remains available visually or on explicit request.
- Speaker decides spoken depth from context, without fixed word-count heuristics.

## P0: Notes and shared-memory failures

### PBS-010: Notes are linear transcript inflation, not synthesis

Evidence:

- 28 turns generated 19 rewrites, 23 open questions, 25+ tracked points and repeated action items.
- The same fact appears under synthesis, tracked points, questions, actions, work state and repeated `Latest additions`.

Root cause:

- Notes regenerate a prose ledger from raw transcript and previous Notes.
- Sections have no ownership, item identity, information budget or deduplication contract.

Acceptance:

- Shared memory stores typed facts separately from the rendered Notes document.
- Notes render a bounded thematic synthesis from typed memory.
- Repeated evidence strengthens or updates an item instead of creating another bullet.

### PBS-011: Atlas self-presentation pollutes meeting synthesis

Evidence:

- One-turn sessions contain full paragraphs describing Atlas's own role.
- Current Synthesis begins with `I am Assistant` instead of meeting substance.

Acceptance:

- Atlas self-description is conversation output, not meeting content.
- A meeting with only a self-presentation request has an empty or minimal meeting synthesis.

### PBS-012: Section ownership is broken

Evidence:

- Participant sections contain one bullet per utterance by `unknown participant`.
- Hypotheses, raw fragments, pending requests and user-addressed questions leak into Topics, Questions and Actions simultaneously.

Acceptance:

- Participants contains unique identified people only.
- Decisions contain accepted commitments only.
- Actions contain owned executable next steps only.
- Questions contain unresolved substantive questions only.
- Hypotheses contain uncertain interpretations only.

### PBS-013: Notes regress behind task truth

Evidence:

- Notes continued marking searches pending after Exa completed.

Acceptance:

- Notes derive task status from canonical task memory at render time.
- Completed results atomically replace pending claims.

### PBS-014: Internal IDs leak into human-facing Notes

Evidence:

- `utt_*` IDs appear throughout the rendered document.

Acceptance:

- Provenance remains structured and inspectable in agent detail views.
- Human-facing Notes contain no internal identifiers.

## P1: Decision and scheduling failures

### PBS-015: Jev context omits running work

Evidence:

- Every audited decision persisted `running_tasks: []`, including turns during active work.

Acceptance:

- Jev receives canonical mission/task state, room activity, silence duration, current/queued speech and expected latency.

### PBS-016: Salience values exceed their expected range

Evidence:

- Persisted salience values include 1.02, 1.04 and 1.08.

Acceptance:

- Probability/confidence outputs are schema-bounded and validated before persistence.

### PBS-017: Timing is always `next_gap`

Evidence:

- Nearly every decision uses the same timing value regardless of room state or urgency.

Acceptance:

- Timing is a semantic plan with expiry, urgency, expected preparation latency and room-state constraints.
- Queued intervention is cancelled or revised when context changes.

### PBS-018: Title generation overfits the latest exchange

Evidence:

- The Heilbronn session ended titled `Presenting Assistant Gene Code Research Tools`.

Acceptance:

- Naming uses durable topics and final synthesis, not the latest turn.
- Titles are revised at meaningful topic shifts and finalization only.

## P1: State and persistence failures

### PBS-019: Agent runs remain `running` after disconnect

Evidence:

- A paused session persists Speaker and Notes runs as running.

Acceptance:

- Disconnect/finalization reconciles all transient runs deterministically.
- Historical runs have terminal states only.

### PBS-020: Database has no schema version or migrations

Evidence:

- SQLite contains only `state` and `sessions` tables.

Acceptance:

- Atlas database has explicit schema metadata and migrations.
- Migration from the old database proves equal row count and payload hashes.

### PBS-021: Public state can become oversized

Evidence:

- Earlier Exa payloads exceeded one megabyte per WebSocket snapshot.

Acceptance:

- Persisted full results and public live projections are separate.
- Snapshot size is bounded and tested.

## P1: Frontend and interaction failures

### PBS-022: Home and workspace are not distinct

Evidence:

- Flow/Board/Notes/Transcript chrome appears where no active session exists.

Acceptance:

- Home presents sessions and creation only.
- Workspace appears only after creating/resuming a session.

### PBS-023: Header controls are visually present but not trustworthy

Evidence:

- Speaker toggle behavior is unclear.
- Spark/star control has no reliable observable effect.

Acceptance:

- Every control has a name, tooltip, active/disabled/focus state, tested action and visible result.
- Controls without legitimate product behavior are removed.

### PBS-024: Navigation requires unnecessary clicks

Acceptance:

- Flow, Board, Notes and Transcript support refined hover preview/activation, keyboard navigation and touch fallback.
- Hover activation has dwell/cancellation behavior to avoid accidental switching.

### PBS-025: No persistent Atlas subtitles

Acceptance:

- Atlas-only subtitles are enabled by default.
- Subtitle remains for the complete spoken turn and never displays participant speech.
- User can disable subtitles.

### PBS-026: Visual identity is generic and inconsistent

Acceptance:

- Atlas owns an editable local SVG logo, favicon, compact mark and wordmark.
- Theme is neutral, premium and neither plain light, plain dark nor green-tinted.
- Empty, loading, disconnected and error states are intentionally designed.

## P1: Tool failures

### PBS-027: Jinko remains unverified

Evidence:

- Health stays `standby`; audited sessions only exercised Exa.

Acceptance:

- A real Jinko request succeeds with the configured key.
- Failure is visible and does not block other tools.

### PBS-028: Tool registry is not fully reflected in conversation

Acceptance:

- One canonical registry supplies schemas, capability descriptions, health and UI nodes.
- Atlas can accurately describe all registered tools through `system_capabilities`.

## P1: Identity and naming failures

### PBS-029: Product identity is duplicated

Evidence:

- The previous product identity and generic assistant label exist across paths, packages, config, database, CLI and UI.

Acceptance:

- Canonical identity is Atlas.
- Repository, local directory, Python package, CLI, config path, config file, data path, database file, product ID and UI all use Atlas.
- Product identity is sourced from one canonical artifact.

### PBS-030: Atlas is not perceived as one coherent participant

Evidence:

- Speaker described itself as `the meeting voice` and denied Worker/tool capabilities.

Acceptance:

- All roles share one Atlas identity.
- Internal roles never present themselves as separate people.
- Atlas speaks in the first person about work performed by its Workers.

## Validation contract

No problem above is closed by prompt edits alone. Closure requires:

1. a deterministic unit or component test where possible;
2. a fixture derived from real session evidence for semantic behavior;
3. an integration test for lifecycle and transport behavior;
4. a visible UI test for user-facing controls and states;
5. a clean full project check;
6. a live Atlas session smoke test after migration.
