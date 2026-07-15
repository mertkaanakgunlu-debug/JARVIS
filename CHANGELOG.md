# CHANGELOG

All notable changes to J.A.R.V.I.S. from Faz 4 onward.
For Iteration 1–3 history, see [log.md](log.md) (frozen 2026-05-24).
For current architecture and feature inventory, see [ProjectState.md](ProjectState.md).

---

## [Faz 8 devam] — 2026-07-15 — GitHub'a taşıma, kalan Faz 0 bug'ları, Flutter doğrulama

- **Repo GitHub'a taşındı**: `langgraph-migration` → `main` fast-forward merge (main 2026-05-09'dan
  beri donmuştu, 67 commit geride kalmıştı, hiçbiri kayıp değildi) + yeni public repo'ya push
  (`github.com/mertkaanakgunlu-debug/JARVIS`). Push öncesi tüm git geçmişi secret taraması yapıldı
  (`.env`/`credentials.json`/`token.json`/`.pem`/`.key` hiç commit edilmemiş; API-key/private-key
  deseni için içerik taraması temiz).
- **Flutter SDK kuruldu** (`C:\flutter`, git clone — winget'te resmi paket yok) ve `mobile/`'da
  `flutter analyze` çalıştırıldı: `ws_client.dart` sıfır hata/uyarıyla derleniyor (Faz 8'in
  BUG-mob-tls/BUG-reconnect/task_a9cee697 Dart değişiklikleri artık gerçekten derlenmiş olarak
  doğrulandı). 69 önceden var olan, bu oturumla ilgisiz `info`-seviye deprecation notu bulundu,
  dokunulmadı. Android SDK yok, `flutter build apk` denenmedi (ayrı, çok daha büyük bir kurulum).
- **`WsClient.reconnect()` artık kendi host/apiKey parametrelerini gerçekten uyguluyor**
  (task_a9cee697) — `_host`/`_apiKey` `final` olduğu için önceden hiç atanmıyordu.
- **4 Faz-0 bug'u düzeltildi** (ROADMAP.md'de "opportunistic, deferred" olarak bırakılmıştı):
  BUG-15 (finance sync regex — `gmail.py`'nin gerçek `"• [id]"` formatına göre düzeltildi, subject/
  body ayrıştırma da aynı kök nedenden dolayı düzeltildi), BUG-16 (todo bg analiz —
  `asyncio.create_task()`'ın sonucu hiç referans tutulmuyordu, GC'lenebiliyordu; modül seviyeli
  strong-ref set eklendi), BUG-17 (gcp_quota cache — tazelik kontrolü hiç yazılmayan bir anahtarı
  arıyordu), BUG-18 (gcp_quota forecast — `last_updated` her kayıtta yenilendiği için günlük oran
  her zaman TÜM zamanların toplamına eşitleniyordu; yeni `first_seen` alanına anchor edildi).
- **`_OllamaEF`/`_GeminiEF.embed_query()` eksikti** (`jarvis/memory.py`) — canlı repro ile bulundu:
  bu projedeki chromadb sürümü `.query()` için koşulsuz `embed_query()` çağırıyor (hasattr fallback
  yok), `.add()` içinse `__call__` — her iki EF de sadece `__call__` içeriyordu, yani her semantik
  recall `AttributeError` fırlatıyordu. İkisine de `__call__`'a delege eden `embed_query()` eklendi.
- **Bonus bulgu, `_GeminiEF`'i canlı Gemini API'sine karşı doğrularken bulundu**: `_build_gemini_ef`'in
  hardcoded model id'si (`"models/text-embedding-004"`) Google tarafında emekliye ayrılmış (her
  çağrı 404 veriyordu). Gerçek `client.models.list()` çağrısı güncel embedding modellerini gösterdi
  (`gemini-embedding-001`/`-2`/`-2-preview`); `gemini-embedding-2`'ye geçildi. Ayrıca construction
  anında bir smoke-test embed çağrısı eklendi (Ollama'nın reachability probe'una benzer) — gelecekte
  Google bir modeli tekrar emekliye ayırırsa bu katman artık her gerçek recall'da çökmek yerine
  hızlıca default ONNX EF'e düşecek.
- **21 scratch worktree branch'inden 17'si silindi** — her biri `git merge-base --is-ancestor` ile
  `langgraph-migration`'a tamamen dahil olduğu doğrulandıktan sonra. 4 tanesi (mainline'da olmayan
  commit'ler içerdiği için) silinmedi — ikisi muhtemelen artık gereksiz, ikisi (eval regression
  suite, rolling/hierarchical summarization) mevcut kodda karşılığı görülmediği için ayrıca
  incelenmeyi bekliyor.
- **12 yeni regresyon testi**, toplam **104/104 test geçiyor**.

---

## [Faz 8] — 2026-07-15 — Temizlik & konsolidasyon (non-destructive scope)

- **`jarvis/legacy/` retired** — the old pydantic-ai orchestrator (unimported by any live path,
  confirmed via grep) and the dead `jarvis/prompts/system.md` pointer file are deleted outright,
  not archived. Recoverable from git history before this commit if ever needed for reference.
- **New minimal test suite** — `tests/` (pytest + pytest-asyncio), 92 tests across 10 files. Covers
  the safety kernel (`policy_guard`'s risk classification, per-action downgrade, kill-switch veto
  scoping), `session_store` concurrency/atomicity, and — the concrete "offline-failover" proof —
  `jarvis/providers/get_llm()`: with no cloud credentials configured at all, both the `fast` and
  `reasoning` roles resolve to bare local Ollama, never a fallback wrapper around nothing. One
  regression test per bug fixed below. New `tests/conftest.py`'s `isolated_cwd` fixture enforces
  MEMORY.md's isolate-test-data-paths lesson for every test that touches cwd-relative storage.
- **9 P2 bugs fixed**: `file_write` ValueError on home paths (BUG-20); calendar dedup hardcoded
  `+03:00` instead of the configured timezone, now DST-correct via `zoneinfo` (BUG-21); IMAP
  connection leak on login failure (BUG-itu); pdf cache keyed on path+mtime instead of content
  hash, so a restored/extracted file with an older mtime could serve stale cached text forever
  (BUG-pdf); Devito-failure fallback hardcoded `duration=0.5` instead of the caller's requested
  duration (BUG-geomath); unanchored `"pro" in text` substring match hijacked ordinary messages
  containing "proje"/"problem"/"program"/etc. into a silent model switch that dropped the user's
  real message (BUG-modelswitch); an empty LLM response was unconditionally accepted as a
  successful turn instead of retried (BUG-emptyresp); `/chat/upload` had no size cap and never
  cleaned up saved files (BUG-upload); `UsageTracker` silently clobbered another live process's
  recorded spend on every save (BUG-usage).
- **Bonus fix, same root cause as BUG-usage, found live**: `kill_switch.py` cached its first
  successful read for the rest of the process's life — a trip from one process (e.g. the CLI's
  `/killswitch`) was invisible to an already-running `--api --monitor` server until restart,
  silently defeating the "hard stop, no prompt" guarantee. Now always re-reads from disk.
- **Electron/mobile client hygiene**: neither Electron REST call (`/chat/upload`, `/chat/stream`)
  sent the `X-API-Key` header — both would 401 once `JARVIS_API_KEY` is configured (BUG-elec).
  Mobile's `/ws` token moved from a `?token=` query param (visible to anything that logs URLs) to
  an `X-API-Key` header via `IOWebSocketChannel`; the server checks the header first, falling back
  to the query param only for Electron, whose browser `WebSocket` API can't set custom headers
  (BUG-mob-tls — closes URL-logging exposure, not wire-level cleartext; this server still has no
  TLS termination). WS reconnect now backs off exponentially (3s → 60s cap) instead of retrying
  every 3s forever (BUG-reconnect).
- **Explicitly out of scope this session** (owner go-ahead required, not assumed): merging
  `langgraph-migration` → `main`; cleaning up the 21 stray `.claude/worktrees/*` scratch branches.

## [Faz 7] — 2026-07-15 — Proaktiflik (software half)

- **New `JarvisAgent.proactive_turn()`** — gives `jarvis/monitor.py` a real path into the
  tool-calling graph (previously toast/FCM notifications only). Runs the exact same compiled graph
  `chat()` does (same tools, same `policy_guard`/kill-switch/audit_log gate — zero changes to any of
  them), on an isolated message list + dedicated LangGraph thread_id that never touches
  `self._history`/`_turn`/`session_store.save_turn` or episodic memory, so JARVIS's background
  self-talk never leaks into the user's real conversation. Serialized via the Faz 0 `_state_lock`
  like every other entry point.
- **Calendar/email self-initiation**: `monitor.py`'s `_check_email()`/`_check_calendar()` now also
  call `_maybe_proactive()` alongside their existing unconditional toast — off by default
  (`MONITOR_PROACTIVE_ENABLED=False`), throttled across all sources combined
  (`MONITOR_PROACTIVE_MIN_GAP_SEC`, default 600s) so a burst of unread emails can't queue many LLM
  calls at once.
- **Confirm-or-notify, not silent execution**: `proactive_turn()` never raises
  `ConfirmationRequired` — no interactive channel exists for a background thread to answer it (same
  constraint `TaskExecutor` already has). A graph interrupt for an L3 action is discarded (never
  resumed, never executed) and reported back so `monitor.py` can notify instead of leaving a
  confirmation pending behind a round-trip nothing consumes yet.
- **`--monitor` now actually works under `--api`** — previously silently ignored (only `cli.py`'s
  branch ever started a `JarvisMonitor`). `api.py`'s `lifespan()` starts one when
  `run_server(..., monitor=True)`; `__main__.py` threads `args.monitor` through.
- **[BUG-19] fixed** — budget/GCP quota alerts had no dedup and re-fired every poll cycle for as
  long as the condition stayed over-threshold. `gcp_quota.quota_alert_check()` now returns
  `(alert_key, message)` pairs; GCP alerts dedup per day, budget alerts dedup per
  `(year, month, category)` — each self-clears on its own natural period rather than needing manual
  reset logic.
- **Live finding, honestly documented, not fully closed**: a real proactive turn against local
  `qwen2.5:7b-instruct` hallucinated an unrelated `procedure_save` call (L2, no-confirm by existing
  design) for a mundane calendar trigger. `_proactive_system_prompt()` now explicitly forbids any
  creating/saving/sending/modifying tool call during a proactive check — investigation stays
  read-only, a suggested action goes in the reply text instead. A prompt-level mitigation on a
  non-deterministic model, not a structural guarantee like the L3 gate — see `docs/SAFETY.md`'s
  "What Faz 7 changed" for the honest residual-risk writeup.
- **Sensor/MQTT proactivity stays deferred** — still blocked on Faz 6 hardware (no Zigbee
  coordinator dongle, no Home Assistant instance).

## [Faz 5] — 2026-07-15 — MCP client layer

- **New `jarvis/mcp_integration.py`** (`McpToolManager`, built on the official
  `langchain-mcp-adapters`) — connects to configured external MCP servers and merges their tools
  into the graph as a second, dynamically-discovered tool source alongside the 36 native `@tool`
  wrappers (dual layer, per the roadmap — the native tools are untouched). Every discovered tool
  gets a `ToolSpec` synthesized at connect time (`tool_registry.register_dynamic_spec()`) inserted
  into the same `TOOL_SPECS` dict the native tools live in, so `policy_guard`, `audit_log`, and the
  async scheduler cover MCP tools identically with zero changes to any of them.
- **Ships with one real server, disabled by default:** Microsoft's official Playwright MCP — real
  browser automation (navigate/click/type/snapshot/screenshot/evaluate JS/…). Flip
  `MCP_PLAYWRIGHT_ENABLED=True` in `.env`. `Settings.mcp_servers` is a generic JSON escape hatch for
  any other server (e.g. a future ha-mcp, Faz 6) — purely additive config, no code changes needed.
- **Fail-closed classification:** a short explicit allow-list of pure-inspection/navigation
  Playwright tool names gets L1/L2 no-confirm; every other tool — including any name never seen
  before — defaults to L3 + `requires_confirmation=True`, the same gate as `shell_run`/`gmail send`.
  Direct mitigation for the prompt-injection risk a browser tool uniquely adds beyond
  `web_search`/`url_read`: a poisoned page can make the model *want* to click/submit something, but
  can't act without the user approving that specific call.
- **Persistent-session architecture** — MCP's stdio transport needs one subprocess alive for a
  session's life for a stateful server like browser automation (confirmed live: the adapter's
  default stateless `client.get_tools()` spawns a fresh process, and fresh blank browser, per tool
  call, silently breaking `navigate` → `click`). `McpToolManager` uses the persistent
  `client.session()` pattern instead, held open by an `AsyncExitStack`. That session is loop-bound
  (same class of constraint as the LangGraph checkpointer), so `JarvisAgent.connect_mcp_tools()` is
  called explicitly, once, from each entry point's real long-lived loop (`cli.py`'s
  `_run_loop`/`_run_voice_loop`, `api.py`'s `lifespan()`) before any turn or `TaskExecutor`
  background job can run — never lazily from whatever caller happens to `chat()` first.
  `build_graph()` gained an `extra_tools` param for this.
- **Windows fix (confirmed live):** `npx` is `npx.cmd`, a batch shim — spawning it directly via
  Python's subprocess APIs raises `WinError 2`. Every npx-based server config goes through
  `cmd /c npx ...`.
- **Bonus fix bundled in:** `policy_guard.describe_call()`'s detail extraction (used in confirmation
  prompts / TTS / audit log) didn't recognize any of Playwright's argument names, so a pending
  `browser_click` confirmation showed just the bare tool name with no indication of what would be
  clicked — added `element`/`url`/`text` to `_DETAIL_KEYS`.

## [Faz 4] — 2026-07-14 — Security kernel + async tools

- **New `jarvis/policy_guard.py`** — transport-agnostic safety kernel (no LangGraph/LangChain
  imports). Single choke point for "is this tool call allowed, does it need the user's OK" —
  `jarvis/graph/nodes.py`'s `confirmation_node` calls it instead of inlining risk checks, so any
  future direct tool dispatcher (MCP, Faz 5) can reuse the same logic rather than reimplementing it.
  **Per-action, not per-tool (BUG-6):** the four mixed-risk `external_api` tools
  (`google_calendar`/`gmail`/`google_drive`/`itu_mail`) have their documented read actions
  (list/search/read/download) downgraded back to L1/no-confirm — only genuinely risky actions
  (send/create/delete/upload/share/...) interrupt.
- **Confirmation gate now defaults on** (`confirmation_gate_enabled=True`, was `False`) and is
  wired into every interface, not just `/chat/confirm` (BUG-3/4): the CLI text REPL catches
  `ConfirmationRequired` and prompts y/n + optional reason; all three voice loops (CLI `--voice`,
  the API wakeword/PTT loop, and the `/ws` remote-audio session) detect `chat_stream()`'s
  `__jarvis_confirm__` JSON marker via new shared helpers in `jarvis/voice/session.py` instead of
  speaking it verbatim, speak a natural question instead, and treat the next utterance as the
  yes/no answer (anything not recognized as affirmative denies, fail-safe). `POST /chat` now
  catches `ConfirmationRequired` before the generic exception handler and returns a structured
  `{"confirmation_required": true, ...}` response instead of an opaque 500
  (BUG-confirm-payload). The system prompt (`prompts/core/02_tool_policy.md`) no longer tells the
  model it never needs to ask (BUG-5) — it now describes the real approve/deny round-trip.
- **Kill switch** — new `jarvis/kill_switch.py`, persisted to `data/kill_switch.json` so a trip
  survives a restart. Scoped to L3 (external-effect) actions; vetoes inside `confirmation_node`
  before the gate would otherwise prompt, skipping the prompt entirely since asking is pointless
  once the operator already said stop. `/killswitch [status|on|off <reason>]` in the CLI.
- **Append-only audit log** — new `jarvis/audit_log.py`, `data/audit_log.jsonl`. Two event kinds
  for every risk_level ≥ 2 tool call: `decision` (policy_guard's ruling, from `confirmation_node`)
  and `execution_start`/`execution_end` (the call actually ran + outcome, from `agent.py`'s
  `_HudEventCallback` — the same LangChain callback attached for every transport).
- **`python_run` reclassified L2→L3 + confirm-required (BUG-1)** — was more powerful than
  `shell_run` (arbitrary unsandboxed Python from any absolute path) while sitting at a lower gate.
  This is the access-control fix; true sandboxing of the subprocess itself is a deferred, not
  claimed, hardening item.
- **SSRF guard for `webfetch.py` (BUG-6-ssrf)** — `url_read`/`deep_web_research` now refuse
  localhost/private/link-local/reserved ranges and cloud metadata endpoints, checked against the
  *resolved* IP so a DNS-rebinding domain can't bypass a hostname-string check.
- **Auth on `/system/wake` (BUG-2)** — new shared `jarvis/api_auth.py` so `api_routers/system.py`
  can require `X-API-Key` without importing `api.py` (avoids a circular import); `/system/ping`
  stays auth-free by design.
- **Recursion cap + agent-node timeout (BUG-recursion, BUG-14)** — new `Settings.
  graph_recursion_limit` (30) passed as LangGraph's `recursion_limit`; `agent_node`'s LLM call
  wrapped in `asyncio.wait_for(timeout=Settings.agent_llm_timeout_sec)` (90s) so a wedged provider
  surfaces a clear error instead of hanging the turn (and, in voice mode, the mic) forever.
- **Async scheduler** — `task_executor.py`'s `ASYNC_KEYWORDS` now genuinely derives from
  `TOOL_SPECS[...].supports_background` instead of only a hand-maintained phrase list (superset of
  the old behavior, no regression). The five sub-agent tool bridges (`math_solve`, `write_content`,
  `research`, `generate_code`, `geo_math`'s analyze branch) plus `todo` converted from sync
  `@tool def` + the `_run_coro()` thread-and-fresh-event-loop bridge to native `async def` `@tool`s
  — `_run_coro()` was dead code afterward and is deleted. Voice loops (API-mode only — the
  standalone CLI `--voice` has no `TaskExecutor`) now hand a flagged query to `TaskExecutor` with a
  spoken acknowledgement instead of blocking the turn in silence for up to minutes; completion adds
  a Windows toast alongside the pre-existing FCM push.
- **Bonus fix found live:** `TaskExecutor._run()`'s background `agent.chat()` call could raise
  `ConfirmationRequired` (it's an `Exception` subclass) with no channel to answer it — previously
  surfaced as a cryptic `"confirmation_required:<uuid>"` failure. Now caught specifically and
  reworded to name the blocked action and point the user at an interactive retry.
- **Explicitly deferred, not this phase:** no Electron/mobile UI renders a confirmation prompt from
  the API's structured response yet (this dev machine still has no Node.js/npm on PATH — same
  constraint as Faz 3's Electron work); `python_run` is gated, not sandboxed; voice confirmation's
  per-call description stays in English technical form even in a Turkish session.

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
