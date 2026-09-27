# Installation

Atlas has a Python backend and a TypeScript browser client. It needs Python 3.12 through `uv`, Bun,
Git and a modern Chromium browser.

## Linux

Install missing tools:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
curl -fsSL https://bun.sh/install | bash
```

Restart the shell, then:

```bash
git clone git@github.com:KpihX/atlas.git
cd atlas
cp backend/.env.example backend/.env
make install
make serve
```

## macOS

Use the official installers above, or Homebrew:

```bash
brew install uv bun make
```

Then use the same clone and `make install` commands as Linux.

## Windows with WSL

WSL is the recommended Windows environment because the repository Makefile uses POSIX shell
commands.

```powershell
wsl --install
```

Open the installed Linux distribution, install `uv` and Bun with the Linux commands, clone Atlas
inside the WSL filesystem, then run `make install` and `make serve`.

## Native Windows PowerShell

Install `uv` and Bun without Python or Node package managers:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
powershell -c "irm bun.sh/install.ps1 | iex"
```

Restart PowerShell, clone the repository, then run the Make-equivalent commands:

```powershell
git clone git@github.com:KpihX/atlas.git
Set-Location atlas
Copy-Item backend/.env.example backend/.env
uv sync --project backend --all-groups
bun install --cwd frontend
uv run --project backend atlas config-init
uv run --project backend atlas export-openapi "$PWD/frontend/openapi.json"
bun run --cwd frontend generate:protocol
bun run --cwd frontend build
$env:ATLAS_FRONTEND_DIR = (Resolve-Path frontend/dist)
uv run --project backend atlas serve
```

## Configuration

Edit:

```text
backend/.env
~/.config/atlas/atlas.json              Linux, macOS and WSL
%USERPROFILE%\.config\atlas\atlas.json Windows native unless XDG_CONFIG_HOME is set
```

At minimum, provide credentials for the active voice, decision and LLM providers. The default demo
voice uses Gradium:

```text
GRADIUM_API_KEY=
GRADIUM_VOICE_ID=
TYPESAFE_API_KEY=
OPENAI_API_KEY=
OPENCODE_ZEN_API_KEY=
EXA_API_KEY=
JINKO_API_KEY=
```

Only keys for enabled features are required. Never commit `backend/.env`.

Key roles and setup locations:

```text
GRADIUM_API_KEY      Gradium dashboard -> API Keys; default STT and TTS
GRADIUM_VOICE_ID     Gradium voice library; optional override
TYPESAFE_API_KEY     TypeSafe dashboard; Jev semantic routing
OPENAI_API_KEY       https://platform.openai.com/api-keys; default LLM roles
OPENCODE_ZEN_API_KEY OpenCode Zen account; optional LLM fallback
EXA_API_KEY          https://dashboard.exa.ai/api-keys; optional web research
JINKO_API_KEY        Jinko dashboard or hackathon allocation; optional travel research
```

## Validate

Linux, macOS and WSL:

```bash
make check
```

Native Windows PowerShell:

```powershell
uv run --project backend ruff check .
uv run --project backend ruff format --check .
uv run --project backend pyright
uv run --project backend pytest
bun run --cwd frontend check
```

## Troubleshooting

`Backend connection is not ready`:

```text
Stop every old Atlas process, run make serve again, then hard-refresh the browser.
Frontend and backend protocol versions must match.
```

No transcription:

```text
Confirm browser microphone permission.
Confirm the session is listening and PCM frames increase.
Check that the active STT provider has valid credentials and credit.
```

No voice:

```text
Confirm Atlas voice is enabled in the header.
Check the active TTS provider and account credit.
Use headphones when shared system audio can feed Atlas output back into capture.
```

System audio chooser returns no sound:

```text
Select a browser tab or screen that exposes audio and enable the browser's share-audio checkbox.
Some operating systems cannot expose audio for every window type.
```
