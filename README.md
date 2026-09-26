# Meeting Sidecar

A headless meeting agent with a replaceable browser client.

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
continuously rewrites one cumulative meeting document from only the newly heard lines.

The voice channel supports barge-in, spoken mute/unmute/end controls, adaptive floor timing and Opus
playback. Ending or leaving a session cancels active audio, queued speech and in-flight synthesis.

`make dev` runs FastAPI with reload and Vite on `http://127.0.0.1:5173`.

## Configuration

Secrets live only in `backend/.env`; start from `backend/.env.example`.

Adjustable runtime configuration is installed once at:

```text
~/.config/meeting-sidecar/config.json
```

Zen Muse is the development generator. Switch production to direct OpenAI by changing one value:

```json
{
  "llm": {
    "active_model": "openai-gpt-5-6-luna"
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
