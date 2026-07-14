# J.A.R.V.I.S. — Architecture Map

> Last updated: 2026-07-14 (Faz 2 — 5-layer cognitive memory).
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
       ├─ planner_node   (/think — reasoning role, step-by-step plan)
       └─ agent_node     (fast role by default, reasoning role for complex queries)
            └─ route_from_agent
                 ├─ tools_node   (LangGraph ToolNode — 35 tools)
                 │    └─ route_from_tools → agent_node (loop until done)
                 └─ critic_node  (reasoning role, accept or revise, up to 2×)
                      └─ route_from_critic → {agent_node | END}
```

**State:** `JarvisState` TypedDict — `messages`, `language`, `plan`, `critique`,
`tool_calls_count`, `critic_iterations`, `session_id`, `user_id`, `workspace`,
`settings`, `memory` (11 fields). See `jarvis/graph/state.py`.

**Key files:**
- `jarvis/agent.py` — `JarvisAgent` wrapper, history management, multimodal pipeline
- `jarvis/providers/__init__.py` — `get_llm(role, settings)` role→provider router (Faz 1)
- `jarvis/graph/graph.py` — `build_graph()` StateGraph builder
- `jarvis/graph/nodes.py` — all node functions + routing predicates
- `jarvis/graph/tools.py` — `make_tools()` factory (35 `@tool` wrappers)
- `jarvis/graph/state.py` — `JarvisState` TypedDict
- `jarvis/graph/streaming.py` — `astream_response()` async text generator

## Models (Faz 1 — role→provider router, `jarvis/providers/get_llm`)

| Role | Primary | Fallback chain |
|---|---|---|
| `fast` / `local` / `realtime` (agent execution) | `qwen2.5:7b-instruct` via Ollama | configured cloud tiers: Vertex Flash, then AI Studio `gemini-2.5-flash` |
| `reasoning` (planner / critic / complex queries) | configured cloud tiers: Vertex Pro, then AI Studio `gemini-2.5-flash` | local Ollama (final fallback — nothing is cloud-mandatory) |
| Sub-agents (5×, pydantic-ai) | `gemini-2.5-pro` / `flash` | Vertex AI (unchanged — not yet migrated to the router) |
| Embeddings (docs / summaries) | `nomic-embed-text` via Ollama | Gemini `text-embedding-004`, then ChromaDB default ONNX |
| Embeddings (conversation memory) | ChromaDB default ONNX (unchanged — already local) | — |

A manual `/model` switch (`switch_model()`) pins the `fast` role to a specific cloud model
(`Settings.pin_cloud_model`), bypassing the local-first default; `reasoning` is never affected by
the pin. See [MEMORY.md](../MEMORY.md) for the local-first pivot rationale.

## Tools (36 registered)

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

## Memory layers (Faz 2 — 5-layer cognitive memory)

The research report's 5-layer taxonomy (Working → Episodic → Semantic → Procedural → Meta).
Working (LLM context window) and Episodic were already solid pre-Faz-2; Semantic,
Procedural, and Meta are Faz 2's additions.

| Layer | Technology | Collections / Tables | Scope |
|---|---|---|---|
| Working memory | LangGraph state / context window | `JarvisState.messages` | current turn only |
| Episodic memory | ChromaDB, default ONNX EF | `jarvis_memory` | **session-scoped** on recall (Faz 2 fix — `Memory.recall(session_id=...)`) |
| Semantic memory | SQLite `sessions.db` (`facts`) + ChromaDB (Ollama `nomic-embed-text` → Gemini → default ONNX) | `facts`, `jarvis_facts` | cross-session by design |
| Procedural memory | SQLite `sessions.db` (`procedures`) + ChromaDB (same EF chain) | `procedures`, `jarvis_procedures` | cross-session by design |
| Meta memory | Markdown, human-edited only | `jarvis/prompts/core/*.md` + `jarvis/prompts/CORE_VERSIONS.md` | write-protected — see [SAFETY.md](SAFETY.md) |
| Document RAG | ChromaDB, Ollama `nomic-embed-text` → Gemini → default ONNX | `jarvis_docs` | cross-session |
| Session summaries | ChromaDB, same EF chain | `jarvis_summaries` | cross-session |
| Sessions / entities | SQLite `sessions.db` | `sessions`, `messages`, `entities` | entities are global (unchanged by Faz 2) |
| Scheduled tasks | SQLite `sessions.db` | `scheduled_tasks` | — |
| Todos | SQLite `sessions.db` | `todos` | — |
| Finance | SQLite `sessions.db` | `transactions`, `budgets` | — |
| Push tokens | SQLite `sessions.db` | `push_tokens` | — |
| Vault | Filesystem markdown | `vault/conversations/`, `vault/notes/`, `vault/reports/` | — |

Semantic (facts) and procedural (workflows) recall are injected into the system prompt
every turn via `ContextBuilder.build()` → `{facts_block}`/`{procedure_block}` placeholders
in `jarvis/prompts/core/06_context_injection.md`. Facts are extracted per-turn by
`jarvis/fact_extractor.py` (mirrors `entity_extractor.py`) and deduped at insert time via
embedding-similarity lookup (`Memory.find_similar_fact`), gated by the same trivial-turn
guard as entity extraction (`JarvisAgent._should_extract`, BUG-25). Procedures seed from
the pre-Faz-2 `prompts/workflows/data_report.md` on first run and grow via the
`procedure_save` tool.

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
| `GET /health` | Liveness check |
| `POST /chat/confirm/{conf_id}` | Resume a Phase 3 gate interrupt (approve/deny/edit) |
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

## Known gaps (tracked in refactor roadmap — see [ROADMAP.md](../ROADMAP.md) for the current,
maintained version of this list; updated 2026-07-14)

| Gap | Phase | Status |
|---|---|---|
| No tool risk metadata / confirmation gate | Phase 2–3 | ✅ shipped 2026-05-24, but not functionally complete — see [SAFETY.md](SAFETY.md) |
| Monolithic `system.md` prompt | Phase 1 | ✅ shipped 2026-05-24 (`jarvis/prompts/core/*.md` + `prompt_loader.py`) |
| Memory retrieval duplicated in `agent.py` | Phase 4 | ✅ shipped 2026-05-24 (`jarvis/context_builder.py`) |
| `TaskExecutor` in-memory only (lost on restart) | Phase 5 | ⬜ not started |
| `JarvisMonitor` not started in `--api` mode | Phase 6 | ⬜ not started |
| No voice barge-in / TTS interruption | Phase 7 | ⬜ not started |
| Sub-agents still on pydantic-ai | Phase 8 | ⬜ not started |
