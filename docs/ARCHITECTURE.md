# J.A.R.V.I.S. — Architecture Map

> Last updated: 2026-07-15 (Faz 5 — MCP client layer).
> Source of truth is always the code; this document summarises it.

## Entry points

| Command | What runs |
|---|---|
| `python -m jarvis` | `cli.py` Rich REPL |
| `python -m jarvis --voice` | `RealtimeVoiceEngine` (Silero-VAD + Whisper + Piper) full-duplex loop, barge-in |
| `python -m jarvis --wakeword` | openwakeword "Hey JARVIS" → voice loop (implies `--voice`) |
| `python -m jarvis --api` | FastAPI REST + WebSocket on :8000; add `--voice`/`--wakeword` to also start the voice task |
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

## Tools (36 native + dynamic MCP)

See [TOOLS.md](TOOLS.md) for the full native-tool list with risk levels. Faz 5 adds a second,
dynamically-discovered tool source — see the MCP layer section below.

## MCP layer (Faz 5 — `jarvis/mcp_integration.py`)

Dual-layer per the roadmap: the 36 native `@tool` wrappers above are untouched; MCP is a purely
additive tool source layered on top, config-driven so a future server (ha-mcp, Faz 6) is just a
config entry, not new code.

- **Config:** `Settings.mcp_playwright_enabled` (dedicated convenience flag for the one server
  shipped this phase — Microsoft's `@playwright/mcp`, real browser automation: navigate, click,
  type, snapshot, screenshot, evaluate JS, …) + `Settings.mcp_servers` (generic escape hatch, a
  JSON `{name: {command, args, transport}}` dict shaped exactly like
  `langchain_mcp_adapters.client.MultiServerMCPClient`'s own constructor argument — any future
  server is additive config, not code).
- **Connection lifecycle (`McpToolManager`, one instance per `JarvisAgent`):** MCP's stdio
  transport keeps ONE subprocess alive for a session's lifetime — required for stateful servers
  like browser automation, where a later `browser_click` must still see the page a prior
  `browser_navigate` opened (confirmed live: the adapter's default *stateless* `get_tools()`
  spawns a fresh process — and fresh, blank browser — per call, silently breaking multi-step
  flows). `McpToolManager` always uses the persistent `client.session()` + `load_mcp_tools(session)`
  pattern instead, held open by an `AsyncExitStack` for the process's life. That session's stdio
  streams are loop-bound (same underlying constraint as the checkpointer — see `graph/graph.py`'s
  docstring), so `JarvisAgent.connect_mcp_tools()` is always called explicitly, once, from the
  *real* long-lived loop (`cli.py`'s `_run_loop`/`_run_voice_loop`, `api.py`'s `lifespan()`) before
  any turn or `TaskExecutor` background job can run — never lazily from whichever caller chats
  first. `chat()`/`chat_stream()` also call it as an idempotent no-op safety net. `build_graph()`
  takes the discovered tools via a new `extra_tools` param and gets rebuilt once they're known
  (`__init__` runs before any loop exists, so the graph is first built MCP-less, same as today with
  MCP disabled — zero regression).
- **Gating — fail-closed by design:** every discovered MCP tool gets a `ToolSpec` synthesized at
  connect time (`tool_registry.register_dynamic_spec()`), so `policy_guard`, `audit_log`, and the
  async scheduler cover it identically to a native tool with zero changes to any of them (they only
  ever call `get_spec()`/read `TOOL_SPECS`). Classification mirrors `policy_guard._READ_ACTIONS`'
  existing per-action override pattern: a short explicit allow-list of pure-inspection
  (`browser_snapshot`, `browser_take_screenshot`, `browser_console_messages`, …) and
  inconsequential-navigation (`browser_navigate`, `browser_wait_for`, …) tool names get L1/L2
  no-confirm; **everything else — including any tool name never seen before, e.g. a future
  Playwright MCP version — defaults to L3 + `requires_confirmation=True`**, same gate as
  `shell_run`/`gmail send`. This is the direct mitigation for the prompt-injection risk a
  browser-automation tool uniquely adds beyond `web_search`/`url_read` (an attacker-controlled page
  can get the model to *want* to click/submit something, but can't actually act without the user
  approving that specific call).
- **Windows gotcha (confirmed live, not assumed):** `npx` is `npx.cmd`, a batch shim — spawning it
  directly raises `WinError 2`. Every npx-based server config goes through `cmd /c npx ...`.

**Verified live (2026-07-15):** `@playwright/mcp` launches under Windows via `langchain-mcp-adapters`;
state persists across separate tool calls in one session (`browser_navigate` then a later
`browser_snapshot` see the same page, both through the actual compiled graph's `ToolNode`, not just
a standalone script); all 24 real tool names get correctly fail-closed-classified ToolSpecs; kill
switch vetoes a tripped L3 MCP call but not an L1 one. **With a real LLM** (`python -m jarvis`,
Gemini 2.5 Pro via Vertex): the model independently decided to navigate (auto-approved, executed,
confirmed via `data/audit_log.jsonl`) and, separately, to click something (correctly logged
`risk_level: 3, outcome: confirm_required` — the gate firing live, not simulated). The CLI's
interactive approve/deny prompt itself wasn't cleanly confirmed over piped stdin (see
[ROADMAP.md](../ROADMAP.md)'s Faz 5 verify section and [HANDOFF.md](../HANDOFF.md) for the
hand-off) — the classification/audit trail is proven live; the terminal UI round-trip needs a real
interactive session to finish confirming.

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
| `GET /ws` | WebSocket HUD event bus — also carries a Faz 3 remote-audio session (binary PCM + control JSON, see below) |
| `POST /voice/ptt/start` | Push-to-talk trigger (local wakeword/PTT loop) |
| `POST /voice/local/pause` / `POST /voice/local/resume` | Faz 3: manually pause/resume the local wakeword/PTT loop (normally automatic — see below) |
| `GET /health` | Liveness check |
| `POST /chat/confirm/{conf_id}` | Resume a Phase 3 gate interrupt (approve/deny/edit) |
| `/todos/*` | Todo CRUD |
| `/finance/*` | Finance queries + budget |
| `/calendar/*` | Google Calendar |
| `/vault/*` | Vault search + recent |
| `/push/*` | FCM push token management |
| `/tasks/*` | Async task status |
| `/system/*` | System info |

## Voice (Faz 3 — real-time local voice + remote audio transport)

`jarvis/voice/` (package, replaces the old flat `jarvis/voice.py`):

| File | Role |
|---|---|
| `engine.py` | `RealtimeVoiceEngine` — transport-blind orchestrator: Silero-VAD end-of-turn segmentation, one-shot Whisper STT at end-of-turn, Piper/edge-tts TTS, barge-in detection. `VoiceModels`/`get_shared_voice_models()` load Whisper/VAD/Piper once and share them across the local engine instance and every remote `/ws` session in the same process. |
| `io_base.py` | `AudioIO` — the thin transport `Protocol` (`raw_frames`, `play_chunk`, `abort_playback`, `mic_level`, …) that keeps VAD/STT/TTS logic out of both transports below |
| `io_duplex.py` | `DuplexAudioIO` — local full-duplex `sounddevice` (callback-mode `InputStream` always open + a per-turn `OutputStream`) |
| `io_remote_ws.py` | `RemoteWsAudioIO` — same contract carried as binary PCM frames over one `/ws` connection instead of local hardware; server-side resample via `scipy` if the client isn't already at 16kHz |
| `session.py` | `drive_voice_session()` — shared turn-taking orchestration (races the next `VoiceEvent` against an in-flight response task so `BargeIn` can cancel it), used by both `cli.py` and `voice_api.py`/`api.py` |
| `session_manager.py` | First-claim-wins arbitration between the local loop and (at most one) remote client — see `jarvis_api_key`-gated auth note below |
| `vad.py` | `SileroVAD` (raw `.onnx` via `onnxruntime` — **not** the `silero-vad` pip package, which hard-requires torch; pinned to v5.1.2, see the file's comment on why v6.2.1 doesn't work with this calling convention), `SustainedGate`/`VadTurnSegmenter` (pure logic) |
| `stt_whisper.py`, `wakeword.py`, `tts_piper.py`, `text.py` | moved from the old `voice.py`, mostly unchanged |

**Remote audio protocol** (Electron today; same protocol designed for a future mobile fast-follow
— see `docs/VOICE_PROTOCOL.md`): a client sends `{"type":"audio_session_start", sample_rate}` over
`/ws`, gets back `audio_session_ack`/`nack`, then exchanges binary PCM16LE mono frames both ways
(mic in, synthesized speech out) plus `audio_format`/`audio_playback_stop`/`audio_session_end`
control messages. Requires `JARVIS_API_KEY` to be configured (rejected with `nack:
"unauthenticated"` otherwise) — once this socket can carry live audio, an unauthenticated
connection is a materially bigger deal than the read-only telemetry it carried before Faz 3.
Starting a remote session auto-pauses the local wakeword/PTT loop's next claim (Electron's main
process always spawns the backend with `--wakeword`) and auto-resumes it when the session ends.

## HUD (Electron)

3 windows: main overlay, settings, mini-orb.
9 panels: chat, schedule, tasks, finance, memory, status, calendar, voice, system.
Event bus: `JarvisEventBus` in `jarvis/ws.py` — per-connection outgoing queue + writer task (Faz 3,
so JSON broadcasts and binary audio chunks never race on one socket), 14 typed emit helpers,
30s/60s/120s cadence. Faz 3: real (not simulated) `mic_level` events replace the renderer's
`useFakeMic` animation whenever a voice session (local or remote) is active; `useRemoteAudioSession`
(renderer) lets the HUD itself be the mic/speaker via `getUserMedia` + an `AudioWorklet`.

## Mobile (Flutter Android)

10 screens: home, chat, schedule, tasks, vault, finance, settings, voice, notifications, calendar.
State: Riverpod. Transport: WebSocket + REST + SSE + FCM.
Kotlin `WakeWordService.kt`: foreground service for always-on wakeword detection — **built but
not wired up** (nothing calls `startWakeWordService()`, and a SharedPreferences key mismatch
breaks the boot-autostart fallback too); pre-existing, unrelated to Faz 3, not fixed.
Voice input today is a separate, independent, on-device `speech_to_text`/`flutter_tts` path (manual
mic button in the chat composer) — does not use Faz 3's remote-audio protocol; that client-side
implementation is an explicitly deferred fast-follow (protocol is already client-agnostic).

## Known gaps (tracked in refactor roadmap — see [ROADMAP.md](../ROADMAP.md) for the current,
maintained version of this list; updated 2026-07-14)

| Gap | Phase | Status |
|---|---|---|
| No tool risk metadata / confirmation gate | Phase 2–3, completed Faz 4 | ✅ shipped 2026-05-24, made functionally complete 2026-07-14 (`jarvis/policy_guard.py` + all-interface wiring) — see [SAFETY.md](SAFETY.md) |
| Monolithic `system.md` prompt | Phase 1 | ✅ shipped 2026-05-24 (`jarvis/prompts/core/*.md` + `prompt_loader.py`) |
| Memory retrieval duplicated in `agent.py` | Phase 4 | ✅ shipped 2026-05-24 (`jarvis/context_builder.py`) |
| `TaskExecutor` in-memory only (lost on restart) | Phase 5 | ⬜ not started |
| `JarvisMonitor` not started in `--api` mode | Phase 6 | ⬜ not started |
| No voice barge-in / TTS interruption | Phase 7 | ✅ shipped 2026-07-14 (new-plan Faz 3 — `jarvis/voice/engine.py`) |
| Sub-agents still on pydantic-ai | Phase 8 | ⬜ not started |
