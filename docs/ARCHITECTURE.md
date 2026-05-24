# J.A.R.V.I.S. — Architecture Map

> Last updated: 2026-05-24 (Faz 21 state).
> Source of truth is always the code; this document summarises it.

## Entry points

| Command | What runs |
|---|---|
| `python -m jarvis` | `cli.py` Rich REPL |
| `python -m jarvis --voice` | `VoiceEngine` STT/TTS loop |
| `python -m jarvis --wakeword` | openwakeword "Hey JARVIS" → voice loop |
| `python -m jarvis --api` | FastAPI REST + WebSocket on :8000 |
| `python -m jarvis --monitor` | `JarvisMonitor` daemon only (no chat UI) |

## Orchestrator — LangGraph StateGraph

```
START
  └─ route_from_start
       ├─ planner_node   (/think — Gemini Pro, step-by-step plan)
       └─ agent_node     (Gemini Flash ReAct executor)
            └─ route_from_agent
                 ├─ tools_node   (LangGraph ToolNode — 35 tools)
                 │    └─ route_from_tools → agent_node (loop until done)
                 └─ critic_node  (Gemini Pro, accept or revise, up to 2×)
                      └─ route_from_critic → {agent_node | END}
```

**State:** `JarvisState` TypedDict — `messages`, `language`, `plan`, `critique`,
`tool_calls_count`, `critic_iterations`, `session_id`, `user_id`, `workspace`,
`settings`, `memory` (11 fields). See `jarvis/graph/state.py`.

**Key files:**
- `jarvis/agent.py` — `JarvisAgent` wrapper, history management, multimodal pipeline
- `jarvis/graph/graph.py` — `build_graph()` StateGraph builder
- `jarvis/graph/nodes.py` — all node functions + routing predicates
- `jarvis/graph/tools.py` — `make_tools()` factory (35 `@tool` wrappers)
- `jarvis/graph/state.py` — `JarvisState` TypedDict
- `jarvis/graph/streaming.py` — `astream_response()` async text generator

## Models

| Role | Model | Provider |
|---|---|---|
| Agent (execution) | `gemini-2.5-flash` | Vertex AI |
| Planner / Critic | `gemini-2.5-pro` | Vertex AI |
| Fallback | `gemini-2.0-flash` / `flash-lite` | AI Studio (free tier) |
| Sub-agents (5×) | `gemini-2.5-pro` / `flash` | Vertex AI |
| Embeddings (memory) | `nomic-embed-text` | Ollama (local) |
| Embeddings (docs) | Gemini embedding | Vertex AI |

## Tools (35 registered)

See [TOOLS.md](TOOLS.md) for the full list with risk levels.

## Sub-agents (pydantic-ai, bridged via `_run_coro`)

| Tool name | Agent | File |
|---|---|---|
| `math_solve` | MathAgent | `jarvis/subagents/math_agent.py` |
| `write_content` | WriterAgent | `jarvis/subagents/writer_agent.py` |
| `research` | ResearchAgent | `jarvis/subagents/research_agent.py` |
| `generate_code` | CoderAgent | `jarvis/subagents/coder_agent.py` |
| `geo_math` | GeoMathAgent | `jarvis/subagents/geomath_agent.py` |

Migration to LangGraph sub-graphs is Phase 8 of the refactor roadmap.

## Memory layers

| Layer | Technology | Collections / Tables |
|---|---|---|
| Semantic memory | ChromaDB + ONNX (`nomic-embed-text`) | `jarvis_memory` |
| Document RAG | ChromaDB + Gemini embeddings | `jarvis_docs` |
| Session summaries | ChromaDB + Gemini embeddings | `jarvis_summaries` |
| Sessions / entities | SQLite `sessions.db` | `sessions`, `messages`, `entities` |
| Scheduled tasks | SQLite `sessions.db` | `scheduled_tasks` |
| Todos | SQLite `sessions.db` | `todos` |
| Finance | SQLite `sessions.db` | `transactions`, `budgets` |
| Push tokens | SQLite `sessions.db` | `push_tokens` |
| Vault | Filesystem markdown | `vault/conversations/`, `vault/notes/`, `vault/reports/` |

## API surface (FastAPI)

| Endpoint | Purpose |
|---|---|
| `POST /chat` | Synchronous chat |
| `POST /chat/stream` | SSE streaming chat |
| `POST /chat/upload` | Multimodal file upload (image/PDF/other) |
| `GET /status` | Session info, model, memory count |
| `POST /reset` | Archive session + clear history |
| `GET /ws` | WebSocket HUD event bus |
| `POST /voice/ptt/start` | Push-to-talk trigger |
| `/todos/*` | Todo CRUD |
| `/finance/*` | Finance queries + budget |
| `/calendar/*` | Google Calendar |
| `/vault/*` | Vault search + recent |
| `/push/*` | FCM push token management |
| `/tasks/*` | Async task status |
| `/system/*` | System info |

## HUD (Electron)

3 windows: main overlay, settings, mini-orb.
9 panels: chat, schedule, tasks, finance, memory, status, calendar, voice, system.
Event bus: `JarvisEventBus` in `jarvis/ws.py` — 13 typed emit helpers, 30s/60s/120s cadence.

## Mobile (Flutter Android)

10 screens: home, chat, schedule, tasks, vault, finance, settings, voice, notifications, calendar.
State: Riverpod. Transport: WebSocket + REST + SSE + FCM.
Kotlin `WakeWordService.kt`: foreground service for always-on wakeword detection.

## Known gaps (tracked in refactor roadmap)

| Gap | Phase |
|---|---|
| No tool risk metadata / confirmation gate | Phase 2–3 |
| Monolithic `system.md` prompt | Phase 1 |
| Memory retrieval duplicated in `agent.py` | Phase 4 |
| `TaskExecutor` in-memory only (lost on restart) | Phase 5 |
| `JarvisMonitor` not started in `--api` mode | Phase 6 |
| No voice barge-in / TTS interruption | Phase 7 |
| Sub-agents still on pydantic-ai | Phase 8 |
