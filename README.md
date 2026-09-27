# Atlas

Atlas is an ambient agent for real conversations. It runs beside a physical meeting, Meet, Teams or
Zoom. It listens through the browser, maintains shared memory, launches useful work in parallel and
speaks when it is addressed or when a contribution is genuinely useful.

Atlas is not a videoconferencing platform and no bot joins the call.

## What It Does

```text
Room audio
    |
    v
STT -> Jev semantic decision -> Shared Memory
                                  |     |      |
                                  |     |      +--> Session naming
                                  |     +---------> Living Notes
                                  +---------------> Curated Board
                                  |
                                  +--> Speaker -> Floor manager -> TTS -> Room
                                  |
                                  +--> Workers -> Exa / Jinko -> findings -> memory
```

During one session Atlas can:

- transcribe microphone and shared system audio;
- answer an explicit request such as `Atlas, research this`;
- notice an implicit evidence gap and investigate without blocking the conversation;
- acknowledge assigned work immediately, then report the result later;
- run Speaker, Workers, Notes, Board and Naming concurrently;
- maintain concise living notes, decisions, questions, actions and verified findings;
- curate durable cards through create, update, merge and explicit retirement operations;
- expose every agent, task, tool call and information flow in the workflow canvas;
- pause, resume, rename, export and delete persistent sessions;
- stop speaking when a participant interrupts it.

## Quick Start

The fastest supported path is Linux, macOS or Windows through WSL.

```bash
git clone git@github.com:KpihX/atlas.git
cd atlas
cp backend/.env.example backend/.env
make install
make serve
```

Open `http://127.0.0.1:8787` and create a session. Fill `backend/.env` with the provider keys you
intend to use. Never commit that file.

For native Windows and machines without `uv`, Bun or Make, follow [INSTALL.md](INSTALL.md).

## Architecture

Atlas uses a microkernel-style architecture. Business behavior depends on typed ports, not provider
SDKs:

```text
FastAPI / WebSocket edge
          |
          v
AtlasEngine microkernel
  session lifecycle, room epochs, task lifecycle, floor and cancellation
          |
          +--> DecisionPort       TypeSafe Jev
          +--> GeneratorPort      OpenAI or OpenCode Zen
          +--> STTPort            Gradium or OpenAI registry
          +--> TTSPort            Gradium or OpenAI registry
          +--> ToolPort           Exa, Jinko, future tools
          +--> StorePort          SQLite
```

The browser is replaceable. It captures audio, renders state and plays PCM, but owns no business
truth. The backend is headless and can serve another UI or transport later.

The detailed behavioral and dependency contract is in [CONTRACT.md](CONTRACT.md).

## Repository

```text
atlas/
|-- backend/
|   |-- src/atlas/
|   |   |-- core/          domain models, engine, agents, ports and policies
|   |   |-- adapters/      Gradium, OpenAI, TypeSafe, Exa, Jinko and SQLite
|   |   |-- api/           HTTP, WebSocket and generated OpenAPI boundary
|   |   |-- llm/           provider-independent model client
|   |   |-- config.py      strict configuration schema
|   |   `-- prompts.py     centralized model-facing instructions
|   `-- tests/
|-- frontend/
|   |-- src/components/    home, controls, workflow, notes and subtitles
|   |-- src/audio.ts       capture, barge-in and streamed playback
|   `-- src/protocol.gen.ts
|-- product.json           canonical identity and protocol version
|-- CONTRACT.md
|-- INSTALL.md
|-- TODO.md
`-- CHANGELOG.md
```

## Persistence

Atlas stores local state in:

```text
~/.config/atlas/atlas.json       adjustable provider and policy configuration
~/.local/share/atlas/atlas.db    SQLite session database
backend/.env                     local secrets, never sent to the browser
```

SQLite contains three Atlas-owned tables:

```text
atlas_schema     schema migration versions
atlas_state      singleton runtime snapshot
atlas_sessions   one canonical JSON state per persistent session
```

Each session state contains transcript, structured memory, cards, tasks, decisions, speeches,
activities and agent runs. Markdown export is generated from this canonical state.

## Provider Configuration

Copy the template first:

```bash
cp backend/.env.example backend/.env
```

On native PowerShell use `Copy-Item backend/.env.example backend/.env`.

| Variable | Role | Required by default | Where to create it |
| --- | --- | --- | --- |
| `GRADIUM_API_KEY` | Live STT and TTS | Yes | Gradium dashboard, API keys |
| `GRADIUM_VOICE_ID` | Override the configured voice | No | Gradium voice library |
| `TYPESAFE_API_KEY` | Jev semantic routing | Yes | TypeSafe project dashboard |
| `OPENAI_API_KEY` | Speaker, Workers, Notes, Board and Naming | Yes | [OpenAI API keys](https://platform.openai.com/api-keys) |
| `OPENCODE_ZEN_API_KEY` | Configured LLM fallback | No | OpenCode Zen account |
| `EXA_API_KEY` | Web research | No | [Exa dashboard](https://dashboard.exa.ai/api-keys) |
| `JINKO_API_KEY` | Flight-calendar research | No | Jinko partner dashboard or hackathon allocation |

Atlas starts without optional tool keys; the corresponding tool is shown as unavailable. Missing
credentials for a selected voice, decision or LLM provider are visible runtime failures.

All model references use `provider/model`. The default role matrix is adjustable in
`~/.config/atlas/atlas.json`:

```json
{
  "llm": {
    "roles": {
      "speaker": "openai/gpt-5-mini",
      "worker": "openai/gpt-4.1-mini",
      "notes": "openai/gpt-4.1-mini",
      "board": "openai/gpt-4.1-mini",
      "naming": "openai/gpt-4.1-nano",
      "fallback": "opencode-zen/muse-spark-1.3"
    }
  }
}
```

STT and TTS are independent registries. Gradium is the recommended working path for the current
demo:

```json
{
  "voice": {
    "stt": {"active": "gradium/default"},
    "tts": {"active": "gradium/default"}
  }
}
```

OpenAI adapters remain registered and switchable by changing only `active`. Their remaining
browser-experience issues are tracked honestly in [TODO.md](TODO.md). If an active provider fails,
Atlas can try the next registered provider. TTS failover occurs only before the first audio chunk so
Atlas never repeats an already spoken prefix.

## Commands

```text
make install    install dependencies, config, protocol and production frontend
make serve      build and run the complete app on 127.0.0.1:8787
make dev        run FastAPI reload and Vite development servers
make check      lint, format-check, type-check, test and production-build
make protocol   regenerate TypeScript types from FastAPI OpenAPI
make status     show repository status
make push       push the current branch to every configured remote
```

## Verification

```bash
make check
```

The gate currently covers backend domain behavior, provider registries, voice failure modes,
semantic routing, task lifecycle, notes, board reconciliation, frontend controls, subtitles,
TypeScript, Pyright, Ruff and a production Vite build.

Live provider checks are available under `backend/scripts/`. They consume API credit and are not run
by the default test suite.

## Known Limits

- Gradium requires available account credit and enforces provider-side session limits. Atlas rotates
  STT sessions before the known limit.
- OpenAI STT and TTS adapters pass isolated provider smokes, but the complete browser conversation
  path still needs stabilization before becoming the recommended default.
- The current SQLite model stores canonical session snapshots. A complete append-only event journal
  remains a planned persistence evolution.
- System-audio sharing depends on browser and operating-system capture permissions.

See [TODO.md](TODO.md) for the concrete next work and [CHANGELOG.md](CHANGELOG.md) for the delivered
release.

## Team

- Ivann Harold KAMDEM POUOKAM
- Sylvain Dehayem
- Pavel Wadoh

Built for the X-IA Rise of Agents hackathon.
