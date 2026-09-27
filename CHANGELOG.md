# Changelog

All notable Atlas changes are recorded here. The project follows semantic versioning while the public
interface stabilizes.

## 0.2.0 - 2026-09-27

### Product

- Renamed the complete product, repository, package, CLI, configuration, database and UI to Atlas.
- Added a redesigned home, persistent session library, workflow canvas, node inspectors, living
  Notes, curated Board, Transcript and accessible session controls.
- Added session creation, resume, rename, Markdown export and deletion.

### Agent Runtime

- Replaced behavioral score thresholds with semantic Jev decisions for addressee, initiative,
  memory, speech depth and timing.
- Added assigned and proactive missions, immediate acknowledgement, visible Worker phases and result
  reporting without blocking the Speaker.
- Added concurrent Speaker, Worker, Notes, Board and Naming execution over shared session memory.
- Added Exa search, Jinko travel tools and capability discovery through one typed registry.

### Memory

- Added structured bounded living memory for synthesis, participants, topics, findings, ideas,
  hypotheses, questions, decisions, recommendations, commitments, actions and current work.
- Added automatic desired-state Board reconciliation with guarded update, merge, creation and
  explicit retirement semantics.
- Added automatic session naming and periodic title revision.

### Voice

- Added microphone plus system-audio capture, multilingual STT, local barge-in, floor management,
  streamed PCM playback, mute/unmute and spoken End controls.
- Added modular STT and TTS registries with `provider/model` selection and safe fallback behavior.
- Added Gradium and OpenAI STT/TTS adapters. Gradium remains the recommended demo path; OpenAI voice
  stabilization remains tracked in `TODO.md`.
- Added provider rotation, reconnect handling and visible failure states.

### Engineering

- Centralized product identity, strict configuration and all model-facing prompts.
- Generated frontend protocol types from FastAPI OpenAPI.
- Migrated 31 historical sessions into Atlas-owned SQLite tables without transcript loss.
- Expanded the gate to backend domain tests, frontend component tests, Ruff, Pyright, TypeScript and
  the production Vite build.

## 0.1.0 - 2026-09-26

- Initial end-to-end ambient meeting prototype with browser capture, Gradium voice, Jev routing,
  model generation, Exa/Jinko tools, SQLite persistence and a React cockpit.
