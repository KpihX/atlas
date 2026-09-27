# TODO

## P0: Demo Readiness

- Record a complete two-minute demo: join a real conversation, explicitly assign research, show a
  proactive investigation, inspect the workflow, open Notes and Board, then export the session.
- Validate a fresh clone on a second machine using only `README.md` and `INSTALL.md`.
- Add one reproducible demo scenario and one clean seeded session without coupling production logic
  to that scenario.

## P0: OpenAI Voice

- Stabilize OpenAI STT in the complete browser capture path. The isolated TTS-to-STT provider smoke
  succeeds, but real mixed browser audio still needs long-session and multilingual validation.
- Stabilize OpenAI TTS playback. The provider returns valid PCM, but previous browser trials produced
  fragmented playback. Keep Gradium as the recommended default until continuous playback is proven.
- Expose the selected STT and TTS provider, fallback event, latency and provider error directly in
  the workflow inspector and health endpoint.
- Add long-session provider failover tests that rotate STT and fail TTS before and after first audio.

## P1: Conversation Quality

- Run multi-speaker acceptance sessions with names, interruptions, background music and system-audio
  echo, then preserve only general semantic regressions as tests.
- Tune proactive interventions from real sessions: useful evidence gaps should launch work while
  speculative or low-value tangents remain silent.
- Measure end-to-end latency from committed utterance to first audible byte for each provider pair.

## P1: Memory and Board

- Continue evaluating automatic Board reconciliation on long sessions with independent concepts,
  clarification, true duplicates and explicit retirement.
- Add a user-visible provenance view linking cards and findings to transcript turns and tool tasks.
- Evolve SQLite snapshots toward an append-only event journal with deterministic replay and immutable
  memory versions.

## P1: Distribution

- Add CI for Linux checks and frontend production build.
- Validate native Windows PowerShell commands and macOS installation on real machines.
- Add a packaged desktop or single-command launcher after the browser prototype stabilizes.
- Decide whether release artifacts should be a Python package, desktop bundle or container image.

## P2: Tools

- Add further tools only through the typed registry and explicit read/write effect classes.
- Add travel reservation proposals with human approval before every external write.
- Add capability discovery in the UI so participants can see available tools without asking Atlas.
