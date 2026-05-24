# J.A.R.V.I.S.
**Just A Rather Very Intelligent System**

A local-first, voice-first, tool-using AI personal assistant. Runs on Windows, talks to your phone over Tailscale, costs ≤$10/mo in API credits.

## What it can do

- **Voice interaction** — wake-word ("Hey JARVIS"), STT via Faster-Whisper, TTS via edge-tts
- **35 registered tools** — files, shell, PDF/Excel/CSV, web search, deep research, LaTeX reports, Python execution
- **Google integrations** — Calendar (batch create, dedup), Gmail, Drive — all via OAuth
- **Spotify playback** control
- **ITU Webmail** — IMAP read + SMTP send
- **Finance tracking** — Burgan Bank statement extraction + budget reporting
- **Geo-math + FDM simulation** — seismic wave modeling, symbolic math, WolframAlpha
- **Long-term memory** — ChromaDB semantic recall + Obsidian-compatible vault
- **REST API + WebSocket HUD** — FastAPI server consumed by the Flutter mobile app
- **Flutter Android app** — chat, schedule, tasks, vault, finance screens + live HUD orb
- **Background monitor** — proactive Gmail/Calendar/todo notifications (Windows toast + FCM push)

## Current State

**Faz 21 complete** — LangGraph orchestration, 35 tools, FastAPI server, Flutter mobile app.
See [ProjectState.md](ProjectState.md) for the full Faz inventory and architecture.

## Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.13+ | Already installed |
| NVIDIA Driver ≥ 555 | For Faster-Whisper GPU inference (RTX 4070) |
| Ollama | For `nomic-embed-text` embeddings — [ollama.com](https://ollama.com/download/windows) |
| MiKTeX | For LaTeX → PDF compilation — [miktex.org](https://miktex.org/download) |
| Gemini API key | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — free tier |
| Tavily API key | [app.tavily.com](https://app.tavily.com) — free tier |
| Google Cloud project | For Vertex AI (optional but recommended for Pro model access) |

## Installation

```powershell
cd C:\Users\mertk\OneDrive\Desktop\Jarvis

# 1. Create virtual environment
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Pull embedding model (requires Ollama running)
ollama pull nomic-embed-text

# 4. Configure secrets
copy .env.example .env
# Edit .env: add GEMINI_API_KEY, TAVILY_API_KEY, and optionally GOOGLE_CLOUD_PROJECT
```

## Running

```powershell
.\.venv\Scripts\Activate.ps1

python -m jarvis                        # Text CLI
python -m jarvis --voice                # Voice mode
python -m jarvis --voice --wakeword     # Always-listening "Hey JARVIS"
python -m jarvis --api                  # FastAPI REST server (port 8000)
python -m jarvis --api --port 9090      # Custom port
python -m jarvis --monitor              # Background watcher only (no chat UI)
```

## CLI Commands

| Command | What it does |
|---|---|
| `/think <message>` | Force planner + critic nodes (Gemini Pro) |
| `/model` | Interactive model switcher |
| `/recall <query>` | Show raw semantic memory results |
| `/status` | Session info, model, memory count, GCP quota |
| `/reset` | Archive session + clear history |
| `/help` | Show all commands |

## Directory Structure

```
Jarvis/
├── README.md               # This file
├── ProjectState.md         # Live status — read at every session start
├── log.md                  # Append-only progress log
├── requirements.txt        # Python dependencies
├── .env                    # Secrets (gitignored)
├── .env.example            # Template
├── jarvis/                 # Python package
│   ├── __main__.py         # Entry point (--voice / --api / --monitor / --wakeword)
│   ├── agent.py            # JarvisAgent — wraps LangGraph graph
│   ├── config.py           # pydantic-settings from .env
│   ├── memory.py           # ChromaDB + Obsidian vault
│   ├── cli.py              # Rich terminal UI
│   ├── api.py              # FastAPI REST + WebSocket HUD
│   ├── api_routers/        # calendar, finance, push, system, tasks, todos, vault
│   ├── monitor.py          # Background watcher daemon
│   ├── voice.py            # VoiceEngine (STT + TTS + VAD + wake-word)
│   ├── graph/              # LangGraph state machine
│   │   ├── graph.py        # StateGraph builder (build_graph)
│   │   ├── nodes.py        # agent, planner, critic nodes + routing
│   │   ├── state.py        # JarvisState TypedDict
│   │   ├── streaming.py    # astream → async text generator
│   │   └── tools.py        # 35 LangChain @tool wrappers (make_tools)
│   ├── tools/              # 18 tool implementation modules
│   ├── subagents/          # math, writer, research, coder, geomath
│   ├── prompts/            # system.md + workflows/
│   └── legacy/             # Old pydantic-ai code (reference only)
├── mobile/                 # Flutter Android app
├── vault/                  # Obsidian-compatible markdown vault
│   ├── conversations/      # Daily session transcripts
│   ├── notes/              # User knowledge base
│   └── reports/            # Generated LaTeX/PDF reports
└── data/                   # ChromaDB, SQLite DBs, caches (gitignored)
```

## Repository layout

| Path | Purpose |
|---|---|
| `jarvis/` | Python package — orchestrator, tools, API, voice, monitor |
| `jarvis/graph/` | LangGraph `StateGraph` (agent, planner, critic, tools nodes) |
| `jarvis/subagents/` | 5 pydantic-ai sub-agents bridged via `_run_coro` |
| `jarvis/prompts/` | System prompt + sub-agent prompts + workflow snippets |
| `jarvis/legacy/` | Rollback copy of pydantic-ai orchestrator (do not import) |
| `electron/` | Electron HUD — 3 windows, 9 panels |
| `mobile/` | Flutter Android app — 10 screens + Kotlin `WakeWordService` |
| `vault/` | Obsidian-compatible markdown vault (conversations gitignored) |
| `data/` | ChromaDB, SQLite `sessions.db`, caches (gitignored) |
| `docs/` | Architecture map, tool list, safety notes |
| `.claude/worktrees/` | **Scratch branches created by Claude Code — never canonical source** |

**Active development branch:** `langgraph-migration`
**Install:** `pip install -r requirements.txt` (canonical; `pyproject.toml` has no dep list)

## Architecture Overview

```
LangGraph StateGraph:
  START → route_from_start
           ├─ planner_node  (/think — Gemini Pro step-by-step plan)
           └─ agent_node    (Gemini Flash ReAct executor)
                └─ tools_node  (executes any of 35 tools)
                └─ critic_node (Gemini Pro — accept or revise up to 2×)
                └─ END
```

- **Primary model:** Gemini 2.5 Pro (Vertex AI) for critic/planner; Flash for execution
- **Fallback:** AI Studio free-tier Flash → Flash-Lite
- **Memory:** ChromaDB (semantic) + SQLite (sessions, todos, schedules, finance) + vault (markdown)

## Completed Phases

| Faz | Name |
|---|---|
| 1–2 | LangGraph migration + graph topology |
| 3–4 | marker-pdf, plotting, data analysis |
| 5 | Vertex AI / Pro-first architecture |
| 6–7 | RAG indexer, deep web research |
| 8 | Spotify + wake-word |
| 9–11 | FastAPI, Google OAuth, HUD WebSocket |
| 12–13 | Entity extractor, session summarizer, scheduler, todos |
| 14–15 | Google Drive, ITU Webmail |
| 16–17 | Finance/Burgan Bank, GCP quota tracker |
| 18 | Geo-math + FDM wave simulation |
| 19–21 | Task executor, WoL, FCM push, Flutter app, HUD panels, multimodal uploads |

---
*Branch: `langgraph-migration` · [ProjectState.md](ProjectState.md) · [log.md](log.md)*
