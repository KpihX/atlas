# Atlas

An ambient collaboration system with a replaceable browser client.

## Run

```bash
make install
make serve
```

Open `http://127.0.0.1:8787`.

Create a new session from the landing screen or resume a previous one. Sessions are persisted in
SQLite and receive a short generated title after their first useful exchange. The footer confirms
whether the browser microphone is active and displays its live input level.

Every saved session can be renamed, exported as Markdown, or deleted with confirmation. The live
board is curated through explicit create/update/merge/delete operations, while a separate notes agent
continuously updates concise structured shared memory from newly heard lines and verified work.

The voice channel supports barge-in, spoken mute/unmute/end controls, adaptive floor timing and Opus
playback. Ending or leaving a session cancels active audio, queued speech and in-flight synthesis.

`make dev` runs FastAPI with reload and Vite on `http://127.0.0.1:5173`.

## Configuration

Secrets live only in `backend/.env`; start from `backend/.env.example`.

Adjustable runtime configuration is installed once at:

```text
~/.config/atlas/atlas.json
```

Models are assigned per role with portable `provider/model` references:

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

Keep the rest of the installed configuration intact. The file is strict: unknown keys and unknown
model IDs stop startup instead of being ignored.

## Checks

```bash
make check
```

The architecture and behavior contract is in `CONTRACT.md`.
