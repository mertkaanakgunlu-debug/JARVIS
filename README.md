# J.A.R.V.I.S.
**Just A Rather Very Intelligent System**

A local-first, hybrid AI personal assistant. Runs on your PC, talks to your phone, costs ≤$10/mo.

## What it can do (full vision)
- Run shell commands, open apps, manage files
- Remember things across sessions (vector memory + Obsidian-style vault)
- Write code, generate reports, create Word/PowerPoint/PDF documents
- Voice interaction with wake-word ("Jarvis…")
- Access from your phone via PWA over Tailscale
- Delegate hard tasks to Claude API; handle routine tasks locally

## Current Iteration: 1 (MVP — Text CLI)
See [ProjectState.md](ProjectState.md) for live status.

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.13+ | Already installed |
| Ollama | latest | [Download](https://ollama.com/download/windows) — install manually |
| NVIDIA Driver | ≥555 | For GPU inference on RTX 4070 |
| Claude API key | — | [Get one](https://console.anthropic.com) — set $10/mo limit |

## Installation

```powershell
cd C:\Users\mertk\OneDrive\Desktop\Jarvis

# 1. Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Pull local models (requires Ollama to be installed and running)
ollama pull qwen2.5:7b-instruct
ollama pull nomic-embed-text

# 4. Configure secrets
copy .env.example .env
# Then edit .env and add your ANTHROPIC_API_KEY
```

## Running

```powershell
.\.venv\Scripts\Activate.ps1
python -m jarvis
```

## Usage

```
> what time is it?                    # handled by local model
> create a file test.txt with "hello" # triggers files tool
> remember my favorite editor is Neovim # stored in memory + vault
> /think refactor this Python class... # forces Claude API
> /help                                # show commands
> /exit                                # quit
```

Special prefixes:
- `/think <message>` — force Claude API for complex reasoning
- `/recall <query>` — show raw memory search results
- `/status` — show which model is active, memory stats
- `/help` — show all commands

## Directory structure

```
Jarvis/
├── README.md          # This file
├── ProjectState.md    # Living state doc — read at session start
├── log.md             # Append-only progress log
├── .env               # Your secrets (gitignored)
├── jarvis/            # Python package
│   ├── agent.py       # Hybrid router + Pydantic-AI agent
│   ├── memory.py      # ChromaDB + vault writer
│   ├── config.py      # Settings
│   ├── cli.py         # Rich terminal UI
│   └── tools/         # shell, files, notes
├── vault/             # Obsidian-compatible markdown vault
│   ├── conversations/ # Daily session transcripts
│   ├── notes/         # User knowledge base
│   └── reports/       # Generated reports (future)
└── data/chroma/       # Vector DB (gitignored)
```

## Roadmap

| Iteration | Name | Status |
|---|---|---|
| 1 | Text CLI + hybrid LLM + memory + tools | ✅ **Current** |
| 2 | Voice push-to-talk (Faster-Whisper + Piper) | ⬜ Planned |
| 3 | Wake-word ("Jarvis…" via Porcupine) | ⬜ Planned |
| 4 | FastAPI server + PWA (phone + desktop browser) | ⬜ Planned |
| 5 | WoL, Spotify, calendar, document generation | ⬜ Planned |

---
*Built with Claude Code · [log.md](log.md) · [ProjectState.md](ProjectState.md)*
