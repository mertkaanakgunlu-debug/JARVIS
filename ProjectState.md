# ProjectState.md
> **Read this first at the start of every new Claude Code session.**
> Update this file after every meaningful change.

> **Note (2026-07-14):** a second, separate phase numbering ("Faz 0-8") started 2026-07-14 for
> the local-first evolution plan — see [ROADMAP.md](ROADMAP.md), [HANDOFF.md](HANDOFF.md),
> [MEMORY.md](MEMORY.md). It is unrelated to this file's "Faz 1-21" feature-inventory numbering
> below (which stops at the 2026-05-23 refactor baseline); Faz 0-3 of the new plan (data-integrity
> stabilization, local-first model router, 5-layer cognitive memory, real-time local voice +
> remote `/ws` audio transport) are done as of this date.
> Reconciling the two numbering schemes into one is deferred to the new plan's own Faz 8 cleanup.

## Current State: Faz 21 + Multimodal Polish (COMPLETE)

**Branch:** `langgraph-migration`
**Last updated:** 2026-05-23

---

## Status: 🟢 Fully Operational

### Done ✅

#### Faz 1 — LangGraph Migration (2026-05-09)
- [x] Replaced pydantic-ai orchestrator with LangGraph `StateGraph`
- [x] `JarvisAgent` public API preserved (`chat`, `chat_stream`, `switch_model`, `reset`)
- [x] `SqliteSaver` checkpointer → per-session cross-turn memory
- [x] Legacy pydantic-ai code retained under `jarvis/legacy/` (superseded 2026-07-15: deleted
      outright in the new plan's Faz 8 cleanup — see [ROADMAP.md](ROADMAP.md))

#### Faz 2 — Graph Topology (2026-05-09)
- [x] `START → route_from_start → [planner →] agent ↔ tools → critic → END`
- [x] `planner_node` — step-by-step planning activated by `/think` prefix (Gemini Pro)
- [x] `critic_node` — quality scoring with up to 2 revision loops (Gemini Pro)
- [x] `agent_node` — ReAct executor (Vertex Flash, AI Studio Flash fallback)
- [x] `graph/state.py` — `JarvisState` TypedDict with `add_messages` reducer

#### Faz 3 — marker-pdf ML Extraction
- [x] `tools/pdf.py` — marker-pdf (ML, ~2-3 GB models, cached) with pdfplumber fallback
- [x] `read_pdf_multimodal()` — returns `(markdown_text, [png_bytes])` for multimodal pipeline
- [x] Cache: `{stem}_{sha256[:8]}.md` + `*_images/` dir for extracted figures

#### Faz 4 — Plotting + Data Analysis
- [x] `tools/plotting.py` — `generate_plot()` — seaborn/matplotlib with auto-format
- [x] `tools/data_analysis.py` — `read_csv_file()`, `analyze_data()` — pandas descriptive stats
- [x] `plot_data` and `csv_read` tools in graph

#### Faz 5 — Vertex AI / Pro-first Architecture
- [x] Dual LLM setup: `llm_fast` (Vertex Flash) + `llm_pro` (Vertex Pro for critic/planner)
- [x] AI Studio (free) fallback chain when Vertex ADC not configured
- [x] `gcp_quota.py` — GCP credit budget tracking (`VERTEX_CREDIT_USD` in .env)
- [x] Cloud primary switched from Flash-Lite → Flash → Pro (progressive upgrades)

#### Faz 6 — RAG Document Indexer
- [x] `tools/indexer.py` — chunk + embed files into ChromaDB via `index_doc` tool
- [x] `vault_search` tool — semantic search across indexed vault content

#### Faz 7 — Deep Web Research
- [x] `tools/webfetch.py` — `fetch_url()` — trafilatura + firecrawl fallback
- [x] `tools/deep_research.py` — multi-step research with citation synthesis
- [x] `url_read`, `deep_web_research` tools in graph

#### Faz 8 — Spotify + Wake-word
- [x] `tools/spotify.py` — spotipy playback control (play, pause, skip, volume, search)
- [x] `spotify` tool in graph
- [x] `--wakeword` flag — openwakeword "Hey JARVIS" hands-free activation
- [x] `voice.py` updated: energy-VAD + wakeword detection in same loop

#### Faz 9 — FastAPI Server + Google Integrations (OAuth)
- [x] `api.py` — FastAPI REST server: `/chat`, `/chat/stream`, `/chat/upload`, HUD WebSocket
- [x] `api_routers/` — 7 routers: calendar, finance, push, system, tasks, todos, vault
- [x] `tools/calendar.py` — Google Calendar API v3: list/create/batch_create/delete/search/update
- [x] `tools/gmail.py` — Gmail API v1: list/read/search/send/label/thread
- [x] `--api` entry point wired in `__main__.py`

#### Faz 10 — Background Monitor + Notifications
- [x] `monitor.py` — `JarvisMonitor`: polls Gmail + Calendar + todos on configurable intervals
- [x] `notify.py` — Windows toast notifications via `winotify`
- [x] `--monitor` standalone mode + combined `--monitor --voice` mode

#### Faz 11 — HUD WebSocket
- [x] `ws.py` — `EventBus` with `tool_call()`, `show_hud()` — broadcasts to all WebSocket clients
- [x] Mobile home screen subscribes to `/ws` for live tool/token feed
- [x] Gemini usage metadata (input/output tokens, model name) pushed to HUD after each LLM call

#### Faz 12-B — Entity Extractor
- [x] `entity_extractor.py` — async Flash-Lite structured-output call after each turn
- [x] Extracts: people, dates, tasks, locations, topics → stored to ChromaDB as metadata

#### Faz 13-A — Session Summarizer
- [x] `session_summarizer.py` — Flash-Lite summary on `/reset` or session archive
- [x] Summary stored in vault + ChromaDB for future recall

#### Faz 13-C — Scheduler Store
- [x] `scheduler.py` — `SchedulerStore`: SQLite-backed scheduled task CRUD
- [x] `schedule` tool in graph (create/list/complete/delete scheduled items)
- [x] Monitor polls due tasks every `MONITOR_SCHEDULE_INTERVAL_SEC` seconds

#### Faz 13-D — Todo Store
- [x] `todo_store.py` — `TodoStore`: SQLite-backed todo CRUD with priority/deadline
- [x] `todo_analyzer.py` — Flash analysis pass for priority ranking
- [x] `todo` tool in graph (add/list/complete/delete/analyze)
- [x] Morning reminder + deadline alerts via monitor

#### Faz 14 — Google Drive
- [x] `tools/drive.py` — Drive API v3: list/search/download/upload/share/delete
- [x] `google_drive` tool in graph
- [x] Local file cache at `data/drive_cache/`

#### Faz 15 — ITU Webmail (IMAP/SMTP)
- [x] `tools/itu_mail.py` — ITU mail via standard IMAP (read) + SMTP (send)
- [x] `itu_mail` tool in graph (list/read/search/send)

#### Faz 16 — Finance / Burgan Bank
- [x] `tools/finance.py` — budget tracking, transaction CRUD
- [x] `finance_store.py` — SQLite finance DB (WAL mode)
- [x] `finance_extractor.py` — parses Burgan Bank statement emails
- [x] `finance_reporter.py` — generates monthly budget reports
- [x] `finance` tool in graph; `api_routers/finance.py` for mobile

#### Faz 17 — GCP Quota Tracker
- [x] `gcp_quota.py` — `quota_status()`, `quota_usage_today()`, `quota_forecast()`
- [x] `usage.py` — `UsageTracker`: per-session token + cost accounting
- [x] `gcp_quota` tool in graph (`status` / `usage` / `forecast` actions)
- [x] Vertex AI credit budget display in CLI `/status`

#### Faz 18 — Geo-math Computation
- [x] `tools/geo_math_tool.py` — symbolic math, FDM wave simulation (Devito), seismic analysis
- [x] `subagents/geomath.py` (via `geo_math` tool) — Gemini Pro sub-agent for complex problems
- [x] `geo_math` tool in graph; triggers `event_bus.show_hud()` for visual outputs
- [x] WolframAlpha integration (optional, `WOLFRAM_APP_ID` in .env)

#### Faz 19 — Task Executor + WoL + Mobile Push + Mobile API
- [x] `task_executor.py` — `TaskExecutor`: background ThreadPoolExecutor for long-running jobs
- [x] `wol.py` — Wake-on-LAN magic packet sender
- [x] `fcm_sender.py` — Firebase Cloud Messaging push notifications to phone
- [x] `push_store.py` — FCM token registry (SQLite)
- [x] `api_routers/push.py` — `/push/register`, `/push/send`
- [x] Mobile API routes: tasks, todos, vault, calendar, finance, system

#### Faz 19A — Flutter Mobile App
- [x] `mobile/` — Flutter app targeting Android
- [x] 10 screens: Home (HUD orb + panels), Chat, Schedule, Tasks, Vault, Finance Detail, Task Detail, Settings, Lock, Finance
- [x] WebSocket provider for live HUD feed
- [x] REST API client (`core/api_client.dart`) with Bearer auth + SSE streaming

#### Faz 20–21 — HUD Panel System + Polish
- [x] Panel visibility system — configurable HUD panels (weather, calendar, tasks, finance, etc.)
- [x] `hud_panels` tool in graph — agent can show/hide panels
- [x] Project Tracker panel with real data
- [x] Image upload: PNG/JPG sent as multimodal base64 blocks directly to LLM
- [x] PDF upload: `read_pdf_multimodal()` → markdown + figures → multimodal message
- [x] Calendar `batch_create` + per-event deduplication guard
- [x] `_strip_images_for_storage()` — images stripped from SQLite session history
- [x] Session auto-reset on FastAPI shutdown
- [x] System prompt: Turkish filler ban, no-narration-before-tool-calls rule

### In Progress 🔄
*(none — working tree clean)*

### Blocked ⛔
*(none)*

### Up Next ⬜
- **Phase 0:** Repository hygiene — `README.md` ✅ done, `ProjectState.md` ✅ done (this file), `.gitignore` hardening for vault data, `Jarvis.rar` cleanup
- ~~**email_triage tool:** defined in `graph/tools.py` but not in the `return` list — verify if intentional or accidental omission~~ — removed entirely in commit `576aa75` (2026-05-24). No longer applicable.
- **Vertex AI ADC:** configure `gcloud auth application-default login` to activate Vertex Pro
- **iOS mobile app:** Android app done; iOS build not started
- **Wake-word tuning:** openwakeword false positive rate on "Hey JARVIS"

---

## Active Configuration (.env)

```
GEMINI_API_KEY=AIza...             ← AI Studio (free tier fallback)
TAVILY_API_KEY=tvly-dev-...        ← web search
GOOGLE_CLOUD_PROJECT=...           ← Vertex AI project (ADC auth)
CLOUD_MODEL=gemini-2.5-pro         ← primary (all user-facing responses)
CLOUD_MODEL_FALLBACK=gemini-2.5-flash
TRIAGE_MODEL=gemini-2.5-flash      ← email triage, session summarizer
VERTEX_MODEL_PRIMARY=gemini-2.5-pro
VERTEX_MODEL_FAST=gemini-2.5-flash
JARVIS_API_KEY=...                 ← REST API auth token
JARVIS_API_PORT=8000
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
EMBED_MODEL=nomic-embed-text
USER_NAME=Sir
CALENDAR_TIMEZONE=Europe/Istanbul
```

---

## Architecture (Current)

```
Entry points:
  python -m jarvis                    CLI (Rich REPL)
  python -m jarvis --voice            Voice (Faster-Whisper STT + edge-tts TTS)
  python -m jarvis --voice --wakeword Always-listening "Hey JARVIS"
  python -m jarvis --api              FastAPI REST + WebSocket HUD (port 8000)
  python -m jarvis --monitor          Standalone background watcher

LangGraph StateGraph (graph/graph.py):
  START → route_from_start
           ├─ planner_node  (Gemini Pro — activated by /think)
           └─ agent_node    (Gemini Flash — tool loop)
                └─ tools_node (ToolNode — executes @tool calls)
                └─ critic_node (Gemini Pro — accept / revise up to 2×)
                └─ END

Models:
  llm_fast  → Vertex Flash (gemini-2.5-flash) — executor
             → AI Studio Flash → Flash-Lite fallback chain
  llm_pro   → Vertex Pro (gemini-2.5-pro) — critic, planner
  triage    → gemini-2.5-flash — email triage, session summarizer, entity extractor

Memory:
  ChromaDB (data/chroma/) — semantic recall, entity metadata, indexed docs
  SQLite (data/sessions.db) — session history, todos, schedules, finance, push tokens
  Obsidian vault (vault/) — daily conversation logs, notes, reports

API (api.py + api_routers/):
  POST   /chat            non-streaming single turn
  POST   /chat/stream     SSE streaming
  POST   /chat/upload     multimodal file upload (image: base64 inline, PDF: marker-pdf)
  GET/POST /tasks, /todos, /vault, /calendar, /finance, /system, /push
  WS     /ws              HUD event feed (tool calls, token counts)

Mobile (mobile/ — Flutter/Android):
  Home screen: HUD orb + configurable panels
  Chat, Schedule, Tasks, Vault, Finance, Settings, Lock screens
```

---

## Tool Registry (36 tools in graph/tools.py)

| # | Tool | Category | What it does |
|---|---|---|---|
| 1 | `shell_run` | System | PowerShell (deny-list guarded) |
| 2 | `file_read` | File | Read text file |
| 3 | `file_write` | File | Write file (creates parents) |
| 4 | `file_list` | File | List directory |
| 5 | `pdf_read` | Document | marker-pdf → markdown (cached) |
| 6 | `pdf_vision` | Document | Gemini Vision for visual PDFs/images |
| 7 | `excel_read` | Document | pandas + header auto-detection |
| 8 | `python_run` | Execution | subprocess .py runner (60 s timeout) |
| 9 | `web_search` | Research | Tavily quick lookup |
| 10 | `note_append` | Memory | Append to vault/notes/ |
| 11 | `report_write` | Report | Write LaTeX .tex |
| 12 | `report_compile` | Report | pdflatex → PDF |
| 13 | `math_solve` | Subagent | MathAgent (Gemini Pro) → LaTeX |
| 14 | `write_content` | Subagent | WriterAgent → academic prose |
| 15 | `research` | Subagent | ResearchAgent → web-augmented LaTeX |
| 16 | `generate_code` | Subagent | CoderAgent → Python/LaTeX |
| 17 | `csv_read` | Data | Read CSV (pandas) |
| 18 | `data_analyze` | Data | Descriptive stats on CSV/Excel |
| 19 | `plot_data` | Data | seaborn/matplotlib plot generation |
| 20 | `report_compose` | Report | Multi-section LaTeX report builder |
| 21 | `vault_search` | Memory | Semantic search across indexed vault |
| 22 | `index_doc` | Memory | Chunk + embed file into ChromaDB |
| 23 | `url_read` | Research | Fetch URL (trafilatura + firecrawl) |
| 24 | `deep_web_research` | Research | Multi-step research + citations |
| 25 | `spotify` | Media | Spotify playback control |
| 26 | `google_calendar` | Google | Calendar list/create/batch_create/delete/search/update |
| 27 | `gmail` | Google | Gmail list/read/search/send/label |
| 28 | `schedule` | Productivity | Scheduled task CRUD |
| 29 | `todo` | Productivity | Todo CRUD + priority analysis |
| 30 | `google_drive` | Google | Drive list/search/download/upload |
| 31 | `itu_mail` | Email | ITU IMAP/SMTP mail |
| 32 | `finance` | Finance | Burgan Bank transactions + budget |
| 33 | `gcp_quota` | System | GCP credit status / forecast |
| 34 | `geo_math` | Science | Geo-math, FDM simulation, seismic |
| 35 | `hud_panels` | UI | Show/hide mobile HUD panels |
| 36 | `procedure_save` | Memory | Save a reusable multi-step workflow to procedural memory (Faz 2) |

*Note (corrected 2026-07-14): `email_triage` was removed entirely from `graph/tools.py` in commit `576aa75` (2026-05-24) — this is no longer a loose end.*

---

## File Map

| File | Purpose |
|---|---|
| `jarvis/__main__.py` | Entry point — `--voice`, `--wakeword`, `--api`, `--monitor`, `--port` |
| `jarvis/agent.py` | `JarvisAgent` — wraps LangGraph, manages session state, model fallback |
| `jarvis/clock.py` | **The** source of "now" (Post-MVP Faz 2) — `SystemClock`/`FrozenClock`, configured from `settings.calendar_timezone`. Consumers: the prompt's now-block, the calendar resolver, `scheduler.py`, `todo_store.py` |
| `jarvis/nlu/temporal.py` | Turkish/English date+time expressions → a timestamp, in the clock's zone, plus a **clock-independent** confidence score that `policy_guard` gates on (Post-MVP Faz 2) |
| `jarvis/nlu/entities.py` | Person-name resolution with confidence bands — a stem is adopted only when a source corroborates it, so "Metin" never becomes "Met" (Post-MVP Faz 2) |
| `jarvis/nlu/event_text.py` | Event titles are a record of what is happening, not a copy of the request; strictly subtractive (Post-MVP Faz 2) |
| `jarvis/config.py` | pydantic-settings from .env; Vertex + AI Studio model config |
| `jarvis/context_builder.py` | `ContextBuilder` — extracted memory/todo/entity/session recall used by `agent.py` (Phase 4, 2026-05-24) |
| `jarvis/tool_registry.py` | `ToolSpec` risk-metadata registry (risk level, confirmation requirement) for all tools (Phase 2, 2026-05-24) |
| `jarvis/memory.py` | ChromaDB recall (episodic/facts/procedures/docs/summaries) + Obsidian vault writer |
| `jarvis/facts_store.py` | SQLite store for semantic-memory facts (Faz 2) |
| `jarvis/fact_extractor.py` | Async durable-fact extraction after each turn (Faz 2) |
| `jarvis/procedure_store.py` | SQLite store for procedural-memory workflows (Faz 2) |
| `jarvis/cli.py` | Rich REPL; `/model`, `/recall`, `/status`, `/reset`, `/help` |
| `jarvis/api.py` | FastAPI REST server + WebSocket HUD (Faz 9 + 11 + 19) |
| `jarvis/api_routers/` | 7 modular API routers |
| `jarvis/monitor.py` | Background daemon — Gmail + Calendar + todos polling |
| `jarvis/notify.py` | Windows toast notifications |
| `jarvis/ws.py` | WebSocket event bus — HUD live feed |
| `jarvis/voice/` | Faz 3 (new plan): `RealtimeVoiceEngine` — Silero-VAD end-of-turn + streaming Whisper STT + Piper local TTS (edge-tts fallback) + barge-in; `AudioIO` protocol (`io_duplex.py` local sounddevice, `io_remote_ws.py` remote `/ws` client); `session.py` turn orchestration; `session_manager.py` local/remote arbitration. Replaces the old flat `jarvis/voice.py` |
| `jarvis/voice_api.py` | Voice loop for API mode (wakeword/PTT-gated, now built on `jarvis/voice/`) |
| `jarvis/graph/graph.py` | LangGraph StateGraph builder |
| `jarvis/graph/nodes.py` | agent_node, planner_node, critic_node, routing functions |
| `jarvis/graph/state.py` | `JarvisState` TypedDict |
| `jarvis/graph/streaming.py` | astream → async text generator |
| `jarvis/graph/tools.py` | 35 LangChain `@tool` wrappers (make_tools factory) |
| `jarvis/tools/` | 18 tool implementation modules |
| `jarvis/subagents/` | math, writer, research, coder, geomath sub-agents |
| `jarvis/entity_extractor.py` | Async entity extraction after each turn |
| `jarvis/session_store.py` | SQLite session history (WAL mode) |
| `jarvis/session_summarizer.py` | Flash-Lite session summary on /reset |
| `jarvis/scheduler.py` | Scheduled task SQLite store |
| `jarvis/todo_store.py` | Todo SQLite store |
| `jarvis/todo_analyzer.py` | Flash priority analysis |
| `jarvis/finance_store.py` | Finance SQLite DB |
| `jarvis/finance_extractor.py` | Burgan Bank email parser |
| `jarvis/finance_reporter.py` | Monthly budget report generator |
| `jarvis/task_executor.py` | Background ThreadPoolExecutor for long jobs |
| `jarvis/usage.py` | Token + cost tracking per session |
| `jarvis/gcp_quota.py` | GCP quota/credit tracker |
| `jarvis/wol.py` | Wake-on-LAN |
| `jarvis/fcm_sender.py` | Firebase Cloud Messaging |
| `jarvis/push_store.py` | FCM device token registry |
| `jarvis/notify.py` | Windows toast |
| `jarvis/prompts/core/*.md` | Modular system prompt (superseded `jarvis/prompts/system.md`, deleted Faz 8) |
| `mobile/` | Flutter Android app |
| `vault/` | Obsidian-compatible markdown vault |
| `data/` | ChromaDB, SQLite DBs, pdf_cache, uploads (gitignored) |

---

## Known Issues / Gotchas

1. **Vertex AI ADC:** `gcloud auth application-default login` required on this machine for Vertex Pro/Flash. Without it, falls back to AI Studio (free tier RPD limits apply).
2. **Gemini free-tier RPD:** gemini-2.5-pro = 25 RPD (free); gemini-2.5-flash = 1500 RPD. Quotas reset midnight UTC.
3. **marker-pdf first run:** downloads ~2-3 GB of layout models to `~/.cache/marker`. Subsequent runs use cache.
4. **MiKTeX path:** `C:\Users\mertk\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.EXE` — hardcoded fallback in `latex.py`.
5. ~~**`email_triage` tool omitted from graph return list** — defined but not exported. Verify intentional.~~ Resolved: removed entirely in commit `576aa75` (2026-05-24).
6. **vault/ gitignore:** conversation transcripts and notes are NOT gitignored. Phase 0 cleanup pending.
7. **Jarvis.rar:** untracked large archive in project root. Should be gitignored or removed.
8. **ChromaDB under OneDrive:** may cause sync churn. Move `CHROMA_DIR` outside OneDrive if noisy.
9. **openwakeword false positives:** "Hey JARVIS" model may trigger on similar-sounding phrases. Threshold tuning may be needed.
10. **ProjectState.md was frozen at Iteration 3** — this rewrite corrects that (2026-05-23).
11. **Never call `datetime.now()` for anything the user will see as a date or time.** Use
    `jarvis/clock.py` (`get_clock()`, or `local_naive_now()` for the stores that persist naive ISO
    strings). Post-MVP Faz 2 exists because "what day is it" had two answers in one process — the
    prompt's now-block read Europe/Istanbul, `calendar.py` read UTC, and for three hours a day that
    disagreement wrote calendar events to the wrong date (measured: 3 of 24 local hours). A `Clock`
    is also the only way to write a test for a bug that only appears between 00:00 and 03:00.
12. **`google_calendar` `create` can run without a confirmation prompt** when date, time, title and
    the user's own wording are all ≥ 0.95 confidence (Post-MVP Faz 2). Every other calendar action
    still asks, the kill switch still vetoes, and `calendar_autonomy_enabled=False` restores the old
    behaviour. See `docs/SAFETY.md` before changing anything in `policy_guard._resolve_risk`.
13. **A local model will resolve dates itself even when told not to.** Measured 10/10 with real
    qwen3:8b: asked for "Pazartesi" it passed an ISO date for a *Saturday* rather than passing the
    word through. Anything that scores "how sure are we about this tool call" must read the user's
    sentence, not only the arguments — arguments are the model's interpretation, not the request.

---

## Next Session Checklist
1. Read this file
2. Check `git log --oneline -5` for recent commits
3. Activate venv: `.\.venv\Scripts\Activate.ps1`
4. Optionally start Ollama for embeddings: `ollama serve`
5. Run JARVIS: `python -m jarvis` (CLI) or `python -m jarvis --api` (server)
6. Verify Vertex ADC if using Pro: `gcloud auth application-default print-access-token`

---

## Completed Roadmap

| Faz | Name | Status |
|---|---|---|
| 1 | LangGraph Migration | ✅ |
| 2 | Graph Topology (planner + critic) | ✅ |
| 3 | marker-pdf ML Extraction | ✅ |
| 4 | Plotting + Data Analysis | ✅ |
| 5 | Vertex AI / Pro-first Architecture | ✅ |
| 6 | RAG Document Indexer | ✅ |
| 7 | Deep Web Research | ✅ |
| 8 | Spotify + Wake-word | ✅ |
| 9 | FastAPI + Google OAuth (Calendar/Gmail) | ✅ |
| 10 | Background Monitor + Notifications | ✅ |
| 11 | HUD WebSocket | ✅ |
| 12-B | Entity Extractor | ✅ |
| 13-A | Session Summarizer | ✅ |
| 13-C | Scheduler Store | ✅ |
| 13-D | Todo Store | ✅ |
| 14 | Google Drive | ✅ |
| 15 | ITU Webmail (IMAP/SMTP) | ✅ |
| 16 | Finance / Burgan Bank | ✅ |
| 17 | GCP Quota Tracker | ✅ |
| 18 | Geo-math + FDM Simulation | ✅ |
| 19 | Task Executor + WoL + FCM Push | ✅ |
| 19A | Flutter Mobile App (Android) | ✅ |
| 20–21 | HUD Panels + Multimodal Polish | ✅ |
