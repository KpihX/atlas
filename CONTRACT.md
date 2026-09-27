# Architecture Contract

> Status: Atlas v0.2 implements the runtime described here. Explicit future evolutions are labelled.

```text
project_id       = "atlas"
contract_version = "5.0.0"
backend          = "Python 3.12 + FastAPI + uv"
frontend         = "TypeScript + React + Vite + Bun"
live_protocol    = "HTTP bootstrap + WebSocket"
storage          = "SQLite canonical session snapshots + projections"
```

The product is an agent sitting beside a meeting. It is not a meeting platform and no bot joins the
call. It hears the room, maintains shared memory, works in the background and remains available for
conversation while work is running.

---

## Central Graph

```text
+--------------------------------------------------------------------------------------------------+
|                                             ATLAS                                                |
+--------------------------------------------------------------------------------------------------+


  ROOM                        REAL-TIME EDGE                     SHARED BACKEND
  ----                        --------------                     --------------

+------------------+       +------------------+       +------------------------------------------+
| Participants     | sound | Browser capture  |  PCM  |                                          |
|                  +------>|                  +------>|              STT ADAPTER                 |
| talk             |       | mic permission   |       |                                          |
| ask              |       | level meter      |       | sound becomes one committed utterance    |
| interrupt        |       | local barge-in   |       | only after semantic silence              |
+--------+---------+       +---------+--------+       +--------------------+---------------------+
         ^                           |                                     |
         |                           | playback state                      | new utterance
         |                           v                                     v
         |                 +------------------+       +------------------------------------------+
         |                 | WebSocket Hub    |<----->|          STATE MUTATION BOUNDARY          |
         |                 |                  |       |                                          |
         |                 | audio in         |       | validates and serializes state changes   |
         |                 | state out        |       | never invents business decisions         |
         |                 +---------+--------+       +--------------------+---------------------+
         |                           |                                     |
         |                           |                                     v
         |                           |                +------------------------------------------+
         |                           +--------------->|              SHARED MEMORY               |
         |                                            |                                          |
         |                                            | current projection of the meeting        |
         |                                            | readable by every agent                  |
         |                                            +-----+--------------+---------------+-----+
         |                                                  |              |               |
         |                      +---------------------------+              |               |
         |                      |                                          |               |
         |                      v                                          v               v
         |        +---------------------------+             +------------------+  +------------------+
         |        |      SPEAKER AGENT        |             | WORKER SUPERVISOR|  | MEMORY AGENTS    |
         |        |                           |             |                  |  |                  |
         |        | always available          |             | long work        |  | notes            |
         |        | no slow tool              |             | Exa / Jinko      |  | board            |
         |        | answers from memory       |             | future tools     |  | naming           |
         |        | explains worker progress  |             |                  |  |                  |
         |        +-------------+-------------+             +---------+--------+  +---------+--------+
         |                      |                                     |                     |
         |                      | proposed speech                     | progress/results    | memory updates
         |                      +------------------+------------------+---------------------+
         |                                         |
         |                                         v
         |                           +---------------------------+
         |                           |       FLOOR MANAGER       |
         |                           |                           |
         |                           | wait for a human gap      |
         |                           | claim floor when bounded  |
         |                           | interrupt on human speech |
         |                           | cancel on End             |
         |                           +-------------+-------------+
         |                                         |
         |                                         v
         |                           +---------------------------+
         |              PCM audio    | TTS registry + sanitizer |
         +---------------------------+ conversational prose only |
                                     +---------------------------+


                                           PERSISTENCE
                                           -----------

                                     +---------------------------+
                                     | SQLite                    |
                                     |                           |
                                     | canonical session state   |
                                     | full tool evidence        |
                                     | session exports           |
                                     +---------------------------+
```

The center of the system is Shared Memory, not a coordinator. Agents never call one another. They
consume one canonical session state and publish typed mutations through the engine boundary.

---

## Shared Memory, Opened

```text
 SOURCES                    MUTATION BOUNDARY                    PROJECTIONS
 -------                    -----------------                    -----------

 human sentence --------+
 worker progress --------+       +--------------------+          +----------------------+
 worker result ----------+------>| typed mutation     |--------->| conversation view    |
 notes revision ---------+       |                    |          | recent turns          |
 board operation --------+       | serialized apply   |          | prior agent speech   |
 playback state ---------+       | session scoped     |          +----------------------+
 session command --------+       | source linked      |
                              | bounded activity   |          +----------------------+
                               +---------+----------+--------->| work view            |
                                         |                     | running tasks         |
                                         |                     | progress              |
                                         |                     | completed evidence    |
                                         |                     +----------------------+
                                         |
                                         |                     +----------------------+
                                         +-------------------->| knowledge view       |
                                                               | living notes         |
                                                               | durable cards        |
                                                               | decisions/actions    |
                                                               +----------------------+
```

```text
                         +------------------------------------+
                         |           SHARED MEMORY            |
                         +------------------------------------+
                         | conversation : what was said       |
                         | work         : what is running      |
                         | knowledge    : what is understood   |
                         | social       : who owns the floor   |
                         | session      : identity and status  |
                         +------------------------------------+
                                      ^
                                      |
                         every agent reads the same snapshot
```

Memory contains facts, not hidden prompts. Complete Exa/Jinko payloads stay in SQLite. Live clients
receive a bounded projection: status, progress, titles, URLs and summaries.

---

## Speaker Agent, Opened

```text
                                      +-----------------------+
 latest utterance ------------------->|                       |
 recent conversation --------------->|                       |
 previous agent speech ------------->|     SPEAKER AGENT     |----> conversational text
 running worker status ------------->|                       |
 completed worker results ---------->|                       |----> optional control
 living notes ---------------------->|                       |      mute / unmute / end
                                      +-----------------------+

 forbidden inputs:

 slow tool execution -----X
 board mutation -----------X
 notes rewriting ----------X
 waiting for Worker -------X
 waiting for Curator ------X
```

The Speaker is permanently available. It answers direct questions, repeats, clarifies, summarizes,
reports progress and explains available results. It never launches or waits for a slow tool.

```text
 Participant: "Search Jev."

 Speaker:     "I am starting that research now."
 Worker:      runs Exa in the background

 Participant: "Where are you?"

 Speaker:     reads Shared Memory immediately
              "Exa has returned five sources. The synthesis is being prepared."
```

No phrase list implements this behavior. A compact memory snapshot gives the Speaker enough context
to understand the request naturally.

---

## Worker Supervisor, Opened

```text
                         +--------------------------+
 work request ---------->|    WORKER SUPERVISOR     |
                         +------------+-------------+
                                      |
                     +----------------+----------------+
                     |                                 |
                     v                                 v
            +------------------+              +------------------+
            | Exa Worker       |              | Jinko Worker     |
            | web evidence     |              | travel evidence  |
            +--------+---------+              +--------+---------+
                     |                                 |
                     +----------------+----------------+
                                      |
                                      v
                         +--------------------------+
                         | progress                 |
                         | evidence                 |
                         | failure                  |
                         | expiry                   |
                         +------------+-------------+
                                      |
                                      v
                               Event Journal
```

```text
 READ TOOL         runs without approval
 LOCAL WRITE       publishes a proposed memory operation
 EXTERNAL WRITE    waits for explicit human approval
```

A Worker never speaks and never mutates cards, notes or session state. It publishes progress and
evidence. Speaker, Notes Agent and Board Curator can use partial or completed results independently.

---

## Memory Agents, Opened

```text
                       +---------------------------+
 new utterances ------>| NOTES AGENT               |
 previous notes ------>|                           |
 worker evidence ----->| rewrite one living        |----> notes revision
                       | document                  |
                       +---------------------------+

                       +---------------------------+
 new utterances ------>| BOARD CURATOR             |
 current cards ------->|                           |
 worker evidence ----->| create / update / merge   |----> board operations
                       | / delete                  |
                       +---------------------------+

                       +---------------------------+
 first useful turns -->| NAMING AGENT              |----> session title
                       +---------------------------+
```

These agents may work concurrently. Their model calls happen outside the state lock. Only applying a
finished revision is serialized, and only when the session ID still matches.

---

## Social Voice, Opened

```text
 speaker proposal
        |
        +---------------------> sanitize for the ear
                                  |
            JSON / code / URL ----X----> rejected
                                  |
                                  v
                         pre-synthesize Opus
                                  |
                                  v
                     +---------------------------+
 room silent ------->|                           |
 room busy --------->|       FLOOR MANAGER       |------> authorize playback
 direct request ---->|                           |
 proactive finding ->|                           |
                     +---------------------------+
                                  |
                    +-------------+--------------+
                    |                            |
                    v                            v
             human starts talking          End / session switch
                    |                            |
                    v                            v
             interrupt immediately        cancel active audio
             publish interrupted          cancel queued speech
                                          cancel in-flight TTS
```

```text
 direct answer      : use the first short natural gap, claim floor after a bounded wait
 proactive finding  : require a longer gap, apply cooldown, remain visual if no value
 mute               : keep hearing and remembering, produce no voice
 unmute             : restore voice without replacing the session
```

At most one playback exists. Human speech owns the floor. The browser stops audio locally before any
network round trip, then reports the interruption to the backend.

---

## Session Space

```text
                              +-------------------+
                              | SESSION LIBRARY   |
                              +---------+---------+
                                        |
                    +-------------------+-------------------+
                    |                                       |
                    v                                       v
          +-------------------+                   +-------------------+
          | New session       |                   | Existing session  |
          | new journal scope |                   | reload projection |
          +---------+---------+                   +---------+---------+
                    |                                       |
                    +-------------------+-------------------+
                                        |
                                        v
                              +-------------------+
                              | Active session    |
                              |                   |
                              | listening         |
                              | paused            |
                              | muted / active    |
                              +---------+---------+
                                        |
                     +------------------+------------------+
                     |                  |                  |
                     v                  v                  v
                  Rename             Export              End
                                        |                  |
                                        v                  v
                                 Markdown archive    cancel voice/work
                                                     persist final state
                                                     return to library

 Existing session actions: Resume | Rename | Export | Delete with confirmation
```

One session ID owns one journal scope. The browser never owns canonical session state.

---

## Source of Truth

```text
 product.json ------------------------------> identity + protocol version

 backend/api/schemas.py --------------------> OpenAPI -----------------> protocol.gen.ts

 backend/config.py + installed atlas.json -> providers + models + timing

 Tool Registry -----------------------------> tool schema + effect class

 SQLite sessions ---------------------------> complete durable truth

 Shared Memory -----------------------------> bounded current projection
```

Secrets live only in `backend/.env`. The browser receives no provider key and no complete private
tool payload.

---

## Physical Implementation Map

```text
backend/src/atlas/
|
+-- core/
|   +-- engine.py              session microkernel and concurrency
|   +-- models.py              canonical typed state
|   +-- ports.py               provider-independent boundaries
|   +-- decisions.py           semantic Jev contract
|   +-- speaker.py             always-available conversational agent
|   +-- coordinator.py         Workers and Board reconciliation
|   +-- notes.py               structured memory and Markdown projection
|   +-- speech.py / voice.py   floor, sanitizer and output policy
|   `-- tools.py               typed tool registry
|
+-- adapters/
|   +-- gradium.py             Gradium STT and TTS
|   +-- openai_stt.py          OpenAI realtime transcription
|   +-- openai_tts.py          OpenAI streamed PCM speech
|   +-- stt_registry.py        active provider and fallback
|   +-- tts_registry.py        active provider and safe fallback
|   +-- typesafe.py            Jev
|   +-- exa.py / jinko.py      read-only tools
|   `-- sqlite.py              canonical session snapshots
|
+-- api/                       HTTP, WebSocket, schemas and composition root
+-- llm/                       provider-independent generator client
+-- config.py                  strict configuration and provider registries
`-- prompts.py                 centralized model-facing instructions
```

Dependency direction:

```text
 API composition root ---------> AtlasEngine ---------> core ports
                                      |                    ^
                                      v                    |
                              canonical state         adapters

 agents  -X-> FastAPI
 agents  -X-> browser
 agents  -X-> provider SDK
 frontend -X-> SQLite
```

---

## Current Runtime

```text
 utterance
     |
     v
    Jev
     |
     +----------> Speaker, immediate and independently vetoed
     |
     +----------> Worker, background and tool-backed
     |
     +----------> Shared Memory
                       |
                       +--> Notes projection
                       +--> Board projection
                       +--> Naming projection
```

Materialized now:

```text
 real microphone and switchable Gradium/OpenAI STT
 multilingual sessions
 SQLite sessions and exports
 Jev decisions
 Exa and Jinko registry
 structured bounded living notes
 agentic board operations
 switchable TTS registry, realtime PCM streaming and browser playback
 barge-in, mute, unmute and End
 visible agent/tool timelines
```

Still to deepen:

```text
 append-only event journal beyond the current activity ledger
 immutable versioned memory snapshots
 explicit Worker Supervisor policy beyond task lifecycle state
 semantic proactive queue expiry and context invalidation
```

These deepen persistence and scheduling without changing the browser protocol or re-coupling Speaker
to Worker execution.

---

## Non-Negotiable Invariants

```text
 Speaker never waits for Worker.
 Worker never speaks.
 Agent never calls another agent directly.
 Every session mutation updates one canonical backend state.
 Every agent reads the same Shared Memory version.
 Only final projection apply is serialized.
 Human speech interrupts playback locally first.
 End cancels active, queued and in-flight voice work.
 Structured tool output never reaches TTS.
 Frontend never reconstructs business truth.
 External writes always require human approval.
```

---

## Failure Space

```text
 component loss          visible consequence              preserved capability
 --------------          -------------------              --------------------
 microphone              no incoming sound                old sessions readable
 STT                     no committed utterance           UI and exports survive
 Jev                     no proactive routing             direct Speaker fallback
 Speaker model           no voice answer                  transcript/work continue
 Worker provider         task failed with evidence        Speaker reports failure
 Notes model             old notes remain versioned       transcript continues
 Board model             old cards remain                 notes/transcript continue
 TTS                     exact text stays visible         meeting work continues
 browser disconnect      active session pauses            state remains durable
 SQLite                  mutations stop                   no fabricated continuity
```

---

## Model and Tool Configuration

```text
 speaker model       = llm.roles.speaker
 worker model        = llm.roles.worker
 notes model         = llm.roles.notes
 board model         = llm.roles.board
 naming model        = llm.roles.naming
 fallback model      = llm.roles.fallback
 fast decision       = TypeSafe Jev

 registered voice providers:
   Gradium STT / TTS
   OpenAI gpt-4o-mini-transcribe / gpt-4o-mini-tts

 registered generators:
   OpenAI GPT-5 mini
   OpenAI GPT-4.1 mini
   OpenAI GPT-4.1 nano
   OpenAI GPT-5.6 Luna
   Zen Muse Spark 1.3 fallback

 registered tools:
   Exa Search      READ
   Jinko Flights   READ
```

Different models may run concurrently. Calls sharing one provider use bounded slots. A failed role
model may fall back to the configured fallback. Configuration selects models through portable
`provider/model` references; business code contains no provider-specific branch.

---

## Verification

```text
 unit contracts        models, stores, decisions, tools, voice sanitizer
 provider loops        Gradium voice plus isolated OpenAI voice smoke
 conversation smoke    direct answer -> repeat -> clarification, no tool
 control smoke         mute -> unmute -> spoken End
 social smoke          playback -> barge-in -> End during pending answer
 browser acceptance    real Edge, Opus decode, console, session library
```

The primary acceptance test is a real conversation in the cockpit. Scripts only isolate a failed
boundary after a real experience exposes it; they are not proof of conversational quality.

---

## Lineage

Heard contributes the live board, living notes and bounded research. `opencode-web-stream`
contributes event-driven wakes, speech queues and barge-in. `meeting-colab` contributes typed effects,
provenance and approval gates. The Proxies contribute contract-first boundaries and transparency.

Reference repositories are read-only inspiration, never runtime dependencies.
## Unified Identity

Every internal role inherits one shared product identity. Participants interact with Atlas,
never with a Speaker model, Worker model, provider, or backend component. The Speaker reads a compact
projection of shared memory and speaks on behalf of completed system work. Capability questions are
routed to the capability-discovery tool, then returned through the same unified voice.
