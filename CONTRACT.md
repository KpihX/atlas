# Architecture Contract

> Status: v0.1 exists. The multi-agent architecture in this contract is the target rebuild.

```text
project_id       = "meeting-sidecar"
contract_version = "4.0.0"
backend          = "Python 3.12 + FastAPI + uv"
frontend         = "TypeScript + React + Vite + Bun"
live_protocol    = "HTTP bootstrap + WebSocket"
storage          = "SQLite event journal + projections"
```

The product is an agent sitting beside a meeting. It is not a meeting platform and no bot joins the
call. It hears the room, maintains shared memory, works in the background and remains available for
conversation while work is running.

---

## Central Graph

```text
+--------------------------------------------------------------------------------------------------+
|                                      MEETING SIDECAR                                             |
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
         |                 | WebSocket Hub    |<----->|              EVENT JOURNAL               |
         |                 |                  |       |                                          |
         |                 | audio in         |       | the ordered history of what happened     |
         |                 | state out        |       | never the owner of business decisions    |
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
         |             Opus audio    | Gradium TTS + sanitizer  |
         +---------------------------+ conversational prose only |
                                     +---------------------------+


                                           PERSISTENCE
                                           -----------

                                     +---------------------------+
                                     | SQLite                    |
                                     |                           |
                                     | complete journal          |
                                     | full tool evidence        |
                                     | session exports           |
                                     +---------------------------+
```

The center of the system is Shared Memory, not a coordinator. Agents never call one another. They
consume memory, publish facts to the journal, and let projectors update memory.

---

## Shared Memory, Opened

```text
 SOURCES                         JOURNAL                         PROJECTIONS
 -------                         -------                         -----------

 human sentence --------+
 worker progress --------+       +--------------------+          +----------------------+
 worker result ----------+------>| ordered facts      |--------->| conversation view    |
 notes revision ---------+       |                    |          | recent turns          |
 board operation --------+       | append only        |          | prior agent speech   |
 playback state ---------+       | session scoped     |          +----------------------+
 session command --------+       | timestamped        |
                               | source linked      |          +----------------------+
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

 backend/config.py + installed config.json -> providers + models + timing

 Tool Registry -----------------------------> tool schema + effect class

 SQLite Journal ----------------------------> complete durable truth

 Shared Memory -----------------------------> bounded current projection
```

Secrets live only in `backend/.env`. The browser receives no provider key and no complete private
tool payload.

---

## Physical Backend Map

```text
backend/src/sidecar/
|
+-- runtime/
|   +-- supervisor.py          agent lifecycle and recovery
|   +-- event_bus.py           publish / subscribe
|   +-- scheduler.py           priorities and bounded concurrency
|
+-- memory/
|   +-- journal.py             append-only session facts
|   +-- shared_memory.py       current immutable snapshot
|   +-- projections.py         conversation / work / knowledge / social
|
+-- agents/
|   +-- speaker.py             always available, no slow tool
|   +-- notes.py               living document
|   +-- board.py               create / update / merge / delete
|   +-- naming.py              session title
|   +-- workers.py             research supervision
|
+-- voice/
|   +-- floor.py               wait / authorize / interrupt
|   +-- sanitizer.py           conversational text boundary
|   +-- playback.py            queue and cancellation
|
+-- tools/
|   +-- registry.py            one extension seam
|   +-- exa.py
|   +-- jinko.py
|
+-- adapters/
|   +-- gradium_stt.py
|   +-- gradium_tts.py
|   +-- typesafe_jev.py
|   +-- sqlite.py
|
+-- api/
    +-- http.py
    +-- websocket.py
    +-- schemas.py
```

Dependency direction:

```text
 API ---------> runtime ---------> agents ---------> ports
                    |                ^                 ^
                    v                |                 |
                 memory             +------------- adapters

 agents  -X-> FastAPI
 agents  -X-> browser
 agents  -X-> provider SDK
 frontend -X-> SQLite
```

---

## Current Implementation Versus Target

```text
 CURRENT v0.1                                  TARGET v0.2
 ------------                                  -----------

 utterance                                     utterance
     |                                             |
     v                                             +----> Speaker, immediate
    Jev                                            |
     |                                             +----> Worker, background
     v                                             |
 global turn lock                                  +----> Notes, background
     |                                             |
 Coordinator                                       +----> Board, background
     |
     +--> tool
     +--> board
     +--> speech

 Result: 9 to 21 second conversational stalls.   Result: no Worker can block Speaker.
```

Already working in v0.1:

```text
 real microphone and Gradium STT
 multilingual sessions
 SQLite sessions and exports
 Jev decisions
 Exa and Jinko registry
 living notes
 agentic board operations
 Opus TTS and browser playback
 barge-in, mute, unmute and End
 visible agent/tool timelines
```

Not yet materialized:

```text
 append-only event journal
 independent Speaker runtime
 Worker Supervisor runtime
 immutable shared-memory projections
 removal of the global conversational lock
```

The next implementation pass starts with Event Journal and Shared Memory, then extracts Speaker.
Workers and memory agents migrate afterward without changing the browser protocol.

---

## Non-Negotiable Invariants

```text
 Speaker never waits for Worker.
 Worker never speaks.
 Agent never calls another agent directly.
 Every durable fact enters through Event Journal.
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
 conversation model  = llm.active_model
 utility model       = llm.utility_model
 fast decision       = TypeSafe Jev

 registered generators:
   OpenAI gpt-5.6-luna
   Zen Muse Spark 1.3
   Zen Mimo 2.6 Flash Free
   Zen Mimo 2.5 Free

 registered tools:
   Exa Search      READ
   Jinko Flights   READ
```

Different models may run concurrently. Calls sharing one provider use bounded slots. A failed utility
model may fall back to the active model. Configuration selects models; business code contains no
provider-specific branch.

---

## Verification

```text
 unit contracts        models, stores, decisions, tools, voice sanitizer
 provider loops        Gradium TTS -> STT in English and French
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
