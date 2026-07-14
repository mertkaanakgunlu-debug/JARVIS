# CHANGELOG

All notable changes to J.A.R.V.I.S. from Faz 4 onward.
For Iteration 1–3 history, see [log.md](log.md) (frozen 2026-05-24).
For current architecture and feature inventory, see [ProjectState.md](ProjectState.md).

---

## [Faz 3] — 2026-07-14 — Real-time local voice + remote `/ws` audio transport

- **Local voice pipeline rebuilt on `jarvis/voice/`** (new package, replaces the flat
  `jarvis/voice.py`): Silero-VAD (raw `.onnx` via `onnxruntime` — not the `silero-vad` pip package,
  which hard-requires torch; pinned to **v5.1.2**, not the newer v6.2.1, after live testing showed
  v6.2.1's exported graph doesn't produce a usable speech-probability signal with the standard
  streaming calling convention despite an identical I/O shape) replaces energy/RMS-threshold VAD
  for end-of-turn detection. **Piper** becomes the primary local TTS engine (Turkish
  `tr_TR-dfki-medium`, English `en_US-lessac-medium`) — chosen over Kokoro-82M specifically because
  Kokoro doesn't support Turkish at all; `edge-tts` stays wired as a per-language fallback tier
  (mirrors the Faz 1 LLM router's local-first-not-cloud-forbidden pattern), not deleted.
- **Full-duplex, not per-turn-blocking:** `DuplexAudioIO` opens one continuously-open callback-mode
  `sounddevice` `InputStream` (previously: a fresh blocking stream per utterance) and a per-turn
  `OutputStream` fed from a thread-safe playback buffer — the mic stays open during playback, which
  is what makes barge-in possible.
- **Barge-in:** sustained, high-confidence speech (deliberately higher threshold + longer duration
  than normal turn-taking, to reduce false triggers from the assistant's own voice bleeding from
  speakers back into the mic — no true acoustic echo cancellation exists in this design) during
  playback aborts audio immediately and cancels the in-flight `agent.chat_stream()` task. Fixed
  **BUG-13** as part of this: `chat_stream()`/`resume_and_stream()` only caught `GraphInterrupt`,
  so a barge-in cancellation (`asyncio.CancelledError`) skipped all turn bookkeeping and left the
  HUD stuck on "thinking"/"speaking" — now propagates correctly while still guaranteeing
  `event_bus.state("idle")` fires.
- **Fixed BUG-12:** the critic's up-to-one-revision loop tags both the draft and the revision
  `langgraph_node="agent"`, so the streamed/voiced text was a run-on concatenation of both with no
  separator — `graph_stream_to_text()` now tracks `metadata["langgraph_step"]` and inserts a
  paragraph break at the pass boundary. `resume_and_stream()`'s independent copy-pasted duplicate
  of the same buggy filter now calls the shared helper instead.
- **Fixed BUG-23:** `openwakeword`'s prediction/mel-spectrogram buffers are now reset
  (`Model.reset()`) at the start of each listening session instead of never.
- **Fixed a wiring gap:** `python -m jarvis --api --voice` (no `--wakeword`) previously started the
  API with zero voice — `--api` never read `args.voice`. `run_server()` now gates on `voice or
  wakeword`.
- **Remote binary audio transport over the existing `/ws` connection** (see new
  `docs/VOICE_PROTOCOL.md` for the full wire spec): a client can act as the mic/speaker for a
  conversation using the server's Whisper/Piper instead of on-device engines. `RemoteWsAudioIO`
  satisfies the same transport-agnostic `AudioIO` protocol as the local `DuplexAudioIO`, so
  `RealtimeVoiceEngine`'s VAD/STT/TTS/barge-in logic is identical either way — no duplication.
  Session arbitration (`jarvis/voice/session_manager.py`) is first-claim-wins between the local
  wakeword/PTT loop and at most one remote client; starting a remote session auto-pauses the local
  loop's next claim (Electron's main process always spawns the backend with `--wakeword`).
  `VoiceModels`/`get_shared_voice_models()` load Whisper/VAD/Piper once and share them across the
  local engine and every remote session in the same process, instead of reloading per-session.
- **`jarvis/ws.py` hardened:** each connection now gets its own outgoing queue + writer task, so
  JSON telemetry broadcasts and binary audio chunks can never race on the same socket — previously
  `broadcast()` was the only thing that ever wrote to a client. Also fixed a latent bug: the `/ws`
  receive loop looped `websocket.receive_text()` forever just to detect disconnects — a binary
  frame would have raised `KeyError` and silently dropped that client, which would have surfaced
  the moment any client sent one. Now branches on `websocket.receive()`'s message type.
- **Security fix pulled forward from the Faz 8 backlog (`BUG-elec`):** the Electron HUD's `/ws`
  connection never sent `?token=`, which mattered little for read-only telemetry but matters a lot
  more once the socket can carry live mic audio and synthesized speech. Electron's main process now
  reads `JARVIS_API_KEY` from the same `.env` file the Python backend reads and passes it to the
  renderer; the server also refuses `audio_session_start` outright when no API key is configured at
  all (remote audio is opt-in-by-configuration).
- **Electron HUD:** `useRemoteAudioSession` (new hook) + `pcm-capture-worklet.js` (new
  `AudioWorkletProcessor`) let the HUD itself become the mic/speaker via `getUserMedia` + Web Audio
  — no new npm dependencies (standard Web Platform APIs). The spacebar handler changed from a
  fire-and-forget PTT POST to a start/stop toggle for this new session type (the old
  `/voice/ptt/start` endpoint is untouched — still serves the local wakeword/PTT path, a parallel
  trigger). The HUD's mic-level meter now shows real telemetry (a new `mic_level` WS event) instead
  of 100% simulated data whenever a voice session is active.
- **Explicitly deferred:** mobile (Flutter) gets no client-side audio-capture/playback code this
  phase — new Dart dependencies, Android runtime mic-permission UX, and real cellular/Tailscale
  jitter are a materially separate effort from the LAN-only Electron implementation. The protocol
  is written down (`docs/VOICE_PROTOCOL.md`) specifically so that fast-follow doesn't require
  reverse-engineering it later. (Also noted, unrelated, not fixed: the Android app already has an
  always-on wake-word service + native overlay + Flutter MethodChannel bridge for a "wake word →
  on-device STT" flow, but the whole chain is disconnected — nothing calls
  `startWakeWordService()`, and a SharedPreferences key mismatch breaks even the boot-autostart
  fallback.)

---

## [Faz 2] — 2026-07-14 — 5-layer cognitive memory

- **Semantic memory:** new `jarvis/fact_extractor.py` (mirrors `entity_extractor.py`) runs a
  Flash-Lite structured-output call after each turn to extract durable facts (stable
  preferences, relationships, recurring constraints — not one-off task detail). New SQLite
  `facts` table (`jarvis/facts_store.py`) + new `jarvis_facts` ChromaDB collection (local-first
  EF chain). Dedup is inline at insert time via embedding-similarity lookup
  (`Memory.find_similar_fact`) — a close match bumps the existing fact instead of inserting a
  duplicate.
- **Fixed global-vs-session recall scoping:** `Memory.recall()` (episodic, `jarvis_memory`) now
  takes an optional `session_id` filter; `ContextBuilder.build()` passes the current session
  through, so a session's raw turns no longer leak into another session's context. Facts and
  session summaries remain deliberately cross-session — that's the point of those layers.
- **Procedural memory:** the hardcoded `_DATA_REPORT_KEYWORDS` keyword match in
  `prompt_loader.py` is gone, replaced by semantic retrieval against a new SQLite `procedures`
  table (`jarvis/procedure_store.py`) + `jarvis_procedures` ChromaDB collection. The pre-existing
  `prompts/workflows/data_report.md` auto-seeds as the first row on startup — zero regression.
  New tool `procedure_save` (#36) lets the agent explicitly persist a new reusable workflow.
- **Meta memory:** `jarvis/tools/files.py`'s `write()` now refuses any path under
  `jarvis/prompts/core/` (`PermissionError`) — persona/safety directives are now provably never
  agent-writable, not just agent-writable-but-not-instructed-to. New
  `jarvis/prompts/CORE_VERSIONS.md` tracks a human-bumped version/updated stamp per core prompt
  file; new `/meta` and `/facts` CLI commands.
- **Fix (BUG-25):** entity extraction (now entity + fact extraction together,
  `_schedule_memory_extraction`) no longer fires on trivially short exchanges (a bare
  "ok"/"tamam" ack) — guarded by `JarvisAgent._should_extract`, verified to still fire for
  `resume_and_stream()`'s legitimate empty-user-text-but-real-response case.
- **Bonus fix (found live during verification, not in the original plan):** the first-pass
  recall-distance thresholds for `recall_facts`/`recall_procedures` were calibrated assuming
  distances in the same range as the pre-existing `recall()`/`recall_summaries()` cutoffs
  (0.5-0.6) — but ChromaDB's default ONNX EF (the fallback whenever Ollama isn't reachable,
  confirmed live-active on this dev machine) produces much larger distances in practice
  (~0.07 paraphrase, ~0.7 related-but-reworded, ~1.7+ unrelated). Recalibrated to 1.1/1.0 based
  on measured values so recall doesn't silently go empty under the fallback EF.

## [Faz 1] — 2026-07-14 — Local-first brain + model router

- **New `jarvis/providers/` module:** `get_llm(role, settings, *, tools=, max_output_tokens=)`
  resolves a role (`fast`/`local`/`realtime`/`reasoning`) to a concrete chat model. Replaces
  `graph.py`'s hardcoded `make_llm_fast`/`make_llm_pro` `ChatGoogleGenerativeAI` factories.
- **Ollama wired as the primary brain:** `fast`/`local`/`realtime` roles resolve to
  `ChatOpenAI` against Ollama's OpenAI-compatible endpoint (`qwen2.5:7b-instruct` by default),
  ahead of cloud — a real `.with_fallbacks()` safety net to whichever cloud tiers are configured
  covers Ollama being unreachable. `reasoning` (critic/planner/complex queries) now targets the
  AI Studio Gemini Flash *free* tier by default (not the 25-RPD Pro tier), with local Ollama as
  its own final fallback so no role is cloud-mandatory anymore.
- **`nomic-embed-text` via Ollama wired as the preferred embedding function** (`jarvis/memory.py`)
  for the `jarvis_docs`/`jarvis_summaries` RAG collections, ahead of the existing Gemini embedder;
  falls through cleanly if Ollama isn't reachable, and never disturbs an already-embedded
  collection's existing EF.
- **Fix (BUG-24):** removed a dead `cloud_tier == "pro"` branch in `effective_cloud_model`
  (`config.py`) — that value is never actually set.
- **Fix (BUG-22):** `switch_model()` now persists its settings onto
  `JarvisAgent._effective_settings`; the quota-exhaustion fallback rebuild in `chat()` uses that
  instead of the original construction-time settings, so it no longer silently discards a user's
  prior manual model switch.
- **Fix (found live while implementing the above, not previously catalogued):** a latent
  `AttributeError` — the pre-Faz-1 `make_llm_fast()` called `.bind_tools()` on the result of
  `.with_fallbacks()`, which doesn't have that method — never hit because recent runs had
  `use_vertex=False`. `get_llm()` now binds tools before wrapping fallbacks.
- **Fix (found live):** `ChatGoogleGenerativeAI`'s constructor validates that an API key is
  present, so eagerly constructing every cloud tier crashed `build_graph()` itself on a
  credential-less (pure-local) config — exactly the setup this phase is supposed to enable. Cloud
  tiers are now built defensively (`_safe_construct()`); a tier that can't even construct is
  dropped from the chain instead of raising.
- Quarantined (header note only, not deleted) the local-model path in
  `jarvis/legacy/agent_pydantic.py` — superseded by `jarvis/providers/get_llm()`; full retirement
  of `jarvis/legacy/` stays Faz 8.
- Environment: this dev machine had zero working model tiers at the start of this phase (Ollama
  not installed, Vertex ADC missing, AI Studio rate-limited) — fixed by installing Ollama and
  pulling both models (owner-approved). Full end-to-end verification then passed live: a real
  `chat()` turn answered via `qwen2.5:7b-instruct (Ollama, local)`, and tool-calling on the local
  model was confirmed directly. See [MEMORY.md](MEMORY.md) for a port-11434 race-condition gotcha
  hit while getting the server running cleanly.

## [Faz 0] — 2026-07-14 — Memory-critical stabilization

- **Fix (BUG-9):** `make_checkpointer()` now returns a real, persistent `SqliteSaver` instead of
  always discarding `db_path` and returning an in-memory `MemorySaver` — LangGraph checkpoints
  (needed to resume a turn interrupted for confirmation) now survive a restart. Custom
  executor-backed async wrapper, not `AsyncSqliteSaver`, since `JarvisAgent` is called from
  multiple independent event loops (`jarvis/graph/graph.py`).
- **Fix (BUG-8):** serialized `JarvisAgent`'s shared mutable state (`session_id`/`_turn`/
  `_history`) with a `threading.Lock` across `chat()`/`chat_stream()`/`resume_and_stream()`/
  `reset()`/`switch_session()`/`switch_model()` — previously unsynchronized across the main loop,
  `TaskExecutor`'s background thread, and `/reset` (`jarvis/agent.py`, `jarvis/api.py`).
- **Fix (BUG-10):** `session_store.py` read methods now take the same lock writers do; `save_turn`
  wraps its DELETE+INSERTs+UPDATE in one explicit transaction.
- **Fix (BUG-11):** `switch_session()` and `JarvisAgent.__init__`'s auto-resume path no longer
  reset the turn counter to 0, which could reuse an old LangGraph `thread_id` and resurrect a
  stale checkpoint into the current conversation (`SessionStore.last_turn_idx()`, new).
- **Fix (found during the above, not previously catalogued):** `session_store.py`'s
  `save_turn`/`load_history`/`load_full_history` were storing a full cumulative snapshot per turn
  but reading across multiple snapshot buckets as if they were deltas, duplicating messages in any
  short conversation. `save_turn` now collapses old buckets; readers select the latest only.
- **Fix (found during the above):** `_schedule_summary_backfill()` leaked an unawaited coroutine
  on every real startup (both entry points construct `JarvisAgent` before an event loop exists).
  Leak fixed; the underlying "backfill runs on startup" feature gap is not — see `BUG-backfill` in
  [ROADMAP.md](ROADMAP.md)'s appendix.
- Environment: `.venv` was rebuilt against Python 3.14.6 (the 3.13 install it depended on had been
  removed from disk); see [MEMORY.md](MEMORY.md).

## [Phase 1–4 refactor] — 2026-05-24 — Prompt modularization, ToolSpec metadata, confirmation gate, memory extraction

- Removed the dead, unregistered `email_triage` tool and its import from `graph/tools.py`
- **Phase 1:** split the monolithic `system.md` into 6 concern files under `jarvis/prompts/core/`, assembled at runtime by `jarvis/prompts/prompt_loader.py`
- **Phase 2:** added `ToolSpec` risk-metadata (`risk_level`, `requires_confirmation`, `side_effect_type`) for all tools in `jarvis/tool_registry.py`
- **Phase 3:** added a LangGraph confirmation node (`jarvis/graph/nodes.py`) + `POST /chat/confirm/{conf_id}` endpoint — interrupts before gated tool calls (not yet fully wired into CLI/voice; see `docs/SAFETY.md`)
- **Phase 4:** extracted memory/todo/entity/session-recall logic out of `agent.py` into `jarvis/context_builder.py` (`ContextBuilder`)
- Fix: `/vault/recent` used a dead `_docs.peek()` reference — switched to `_docs_collection.peek()`

## [Faz 21] — 2026-05-17 — Multimodal uploads, calendar batch_create, HUD panel visibility

- Image/PDF/file upload endpoint (`/chat/upload`) with 3-pipeline routing
- Calendar `batch_create` with dedup and natural-language date parsing
- Panel visibility system in Electron HUD (9 panels, per-panel show/hide)
- Fix: image upload refusal — mandatory `pdf_vision` instruction + system.md rule
- Fix: widget visibility, overlay position, calendar placeholder, hallucination rules

## [Faz 19–20] — 2026-05-15/16 — Task executor, WoL, FCM push, Flutter app

- `TaskExecutor` + `AsyncTask` — async long-running jobs with status polling
- Wake-on-LAN support
- Firebase Cloud Messaging (FCM) push notifications to Android app
- Flutter Android app (10 screens): chat, schedule, tasks, vault, finance, settings
- Kotlin `WakeWordService.kt` foreground service for always-on wakeword

## [Faz 18] — Geo-math + FDM wave simulation

- `geo_math` tool: symbolic math, WolframAlpha, seismic wave FDM modeling
- `GeoMathAgent` (pydantic-ai, Gemini Pro)

## [Faz 16–17] — Finance / GCP quota

- Burgan Bank statement parsing + budget tracking
- `finance` tool: sync, summary, top categories, chart, budget management
- GCP quota tracker (`gcp_quota` tool)

## [Faz 14–15] — Google Drive + ITU Webmail

- `google_drive` tool: search, list, read, download, upload, share, delete
- `itu_mail` tool: IMAP read + SMTP send for akgunlu22@itu.edu.tr

## [Faz 12–13] — Entity extractor, summarizer, scheduler, todos

- Automatic entity extraction from conversations → `jarvis_memory`
- Session summarizer → `jarvis_summaries` ChromaDB collection
- `schedule` tool: add/list/pause/resume/done/delete scheduled tasks (SQLite)
- `todo` tool: add/list/edit/done/delete/today/analyze todos (SQLite)

## [Faz 9–11] — FastAPI, Google OAuth, HUD WebSocket

- FastAPI REST server (`python -m jarvis --api`)
- Google Calendar + Gmail + Drive OAuth integration
- WebSocket HUD event bus (`JarvisEventBus`) — 30s/60s/120s live data cadence
- `JarvisMonitor` daemon for proactive email/calendar/schedule notifications

## [Faz 5–8] — Vertex AI, RAG, Spotify, wake-word

- Vertex AI Gemini 2.5 Pro for planner/critic; Flash for execution
- RAG: `index_doc` + `vault_search` over `jarvis_docs` ChromaDB collection
- `deep_web_research` + `url_read` (trafilatura + firecrawl)
- Spotify Web API (`spotify` tool)
- openwakeword "Hey JARVIS" (`--wakeword` mode)

## [Faz 3–4] — marker-pdf, plotting, data analysis

- `pdf_read` with marker-pdf (falls back to pdfplumber)
- `plot_data`, `data_analyze`, `csv_read`, `excel_read` tools
- LaTeX report pipeline (`report_write` + `report_compile`)

## [Faz 1–2] — LangGraph migration

- Replaced pydantic-ai orchestrator with LangGraph `StateGraph`
- Graph topology: `START → route_from_start → {planner,agent} → tools → critic → END`
- 5 pydantic-ai sub-agents (math/writer/research/coder/geomath) retained via `_run_coro` bridge
