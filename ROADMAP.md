# ROADMAP.md — What's next (Local-First Evrim Planı)

> Completed work (Faz 1-21 features, refactor Phase 1-4) is inventoried in
> [ProjectState.md](ProjectState.md) and [CHANGELOG.md](CHANGELOG.md). This file is
> forward-looking only.
>
> **Restructured 2026-07-14** around the approved local-first phased plan. Full rationale,
> resolved design forks, and per-phase detail live in the plan file:
> `C:\Users\mertk\.claude\plans\c-users-mertk-downloads-ki-isel-jarvis-gentle-phoenix.md`.
> The 59-finding review backlog is preserved in the **Bug backlog appendix** at the bottom and
> mapped into the phases below by `[BUG-n]` tags.

## Direction (owner decisions, 2026-07-14)

- **Local-first.** Vertex credits likely expired; RTX 4070 available. Ollama/Qwen becomes the
  primary brain; Gemini free tier is optional escalation for hard reasoning. Voice goes local
  (streaming Whisper + Kokoro/Piper). See [MEMORY.md](MEMORY.md) for the VRAM budget + model rationale.
- **Memory + intelligence first**, then voice, then (deferred) IoT.
- **IoT deferred** — owner has only an RP2040 today; the physical-world phases are hardware-gated.

## Phase overview

| Faz | Ad | Gate / neden burada | Efor | Durum |
|---|---|---|---|---|
| 0 | Hafıza-kritik stabilizasyon | Faz 1-2'nin temeli | M | ✅ done (2026-07-14) |
| 1 | Local-first beyin + model router | "Zeka" yarısı; hafıza/ses/offline'ı açar | M-L | ✅ done (2026-07-14) |
| 2 | 5 katmanlı bilişsel hafıza | **1. öncelik**; salt-yazılım | L | ✅ done (2026-07-14) |
| 3 | Gerçek zamanlı yerel ses | En yüksek UX; Faz 1'e bağlı | XL | ✅ done (2026-07-14) |
| 4 | Güvenlik çekirdeği + async araç | Otonomi/MCP/IoT ön koşulu | L | ⬜ |
| 5 | MCP katmanı | IoT yazılım ön koşulu | M | ⬜ |
| 6 | Fiziksel dünya / IoT | ⛔ Donanıma bağlı (Faz 0+4+5) | L + HW | ⬜ deferred |
| 7 | Proaktiflik | Capstone; kısmen donanıma bağlı | L | ⬜ deferred |
| 8 | Temizlik & konsolidasyon | — | M | ⬜ |

Faz 1+2 = "hafıza + zeka" ilk bloğu (ikisi de yerel).

---

## Faz 0 — Hafıza-Kritik Stabilizasyon (minimal) ✅ done (2026-07-14)

Only the data-integrity/concurrency subset that would corrupt the memory work in Faz 1-2. NOT
the full safety kernel (that's Faz 4).

- [x] **[BUG-9]** Make `make_checkpointer()` honor `db_path` (real `SqliteSaver`) — today it
      ignores it and returns an in-memory `MemorySaver`, so checkpoints never persist and grow
      unbounded (`jarvis/graph/graph.py:98`).
      Fixed via a custom `_AsyncCompatibleSqliteSaver` (sync `SqliteSaver` + executor-backed async
      methods) rather than `AsyncSqliteSaver` — the latter binds to one event loop at construction,
      but `JarvisAgent` is called from several independent loops (CLI/uvicorn's main loop, and
      `TaskExecutor`'s per-call `asyncio.run()` on a background thread). Each delegate method must be
      a real `async def` (not `def` returning the executor Future) — LangGraph's eager task factory
      calls `asyncio.iscoroutine()` on the result, which a bare `Future` fails.
- [x] **[BUG-8]** Serialize access to the shared `JarvisAgent` singleton — `session_id`/`_turn`/
      `_history` are mutated with no lock from the main loop, `TaskExecutor`'s background thread,
      and `/reset` concurrently (`jarvis/agent.py`).
      Fixed with a `threading.Lock` (not `asyncio.Lock` — callers span independent event loops, not
      just tasks on one loop) held across each full turn in `chat()`/`chat_stream()`/
      `resume_and_stream()`, acquired via `_acquire_state_lock()` which offloads the blocking
      `.acquire()` to an executor so it never freezes the calling event loop. `reset()`/
      `switch_session()`/`switch_model()` take it too; the `/reset` API endpoint now offloads the
      call via `run_in_executor` (matching the pre-existing `lifespan()` shutdown pattern) since it's
      a blocking sync acquire.
- [x] **[BUG-10]** Fix `session_store.py` read/write lock asymmetry (only writers took
      `self._lock`) and make `save_turn`'s DELETE+INSERTs+UPDATE one transaction (`session_store.py:186`).
      All read methods now take `self._lock` too; `save_turn` wraps its statements in an explicit
      `BEGIN IMMEDIATE`/`COMMIT`/`ROLLBACK` (needed because the connection uses
      `isolation_level=None` autocommit).
- [x] **[BUG-11]** Fix `switch_session()` resetting `_turn` to 0 → causes `thread_id` collisions
      against old checkpoints, corrupting history on session re-entry (`jarvis/agent.py:333`).
      Fixed via new `SessionStore.last_turn_idx(session_id)`, used by both `switch_session()` and
      `JarvisAgent.__init__`'s auto-resume path (which had the *same* bug — it also unconditionally
      set `_turn = 0`, so this bit on every restart of a session with prior turns, not just on
      manual `/session` switches).
- [ ] (opportunistic, deferred) broken features that also touch stores: **[BUG-15]** finance sync
      regex, **[BUG-16]** todo('add') background analysis, **[BUG-17/18]** gcp_quota cache +
      forecast. Not done — out of scope once the core 4 turned up two additional bugs (below).

**Bonus findings (not in the original 59-finding review, discovered while fixing the above):**

- **History duplication in `session_store.py`** (now fixed): `save_turn()` was writing a FULL
  cumulative snapshot per `turn_idx` bucket (not a delta), but `load_history()`/`load_full_history()`
  read across *multiple* `turn_idx` buckets and concatenated them — duplicating every message that
  appeared in more than one bucket. Visible any time the fetched row count didn't hit the trim
  limit (i.e. early in any conversation, before ~20 messages) — auto-resume on startup or
  `switch_session()` would silently load a session history with messages repeated 2-3x, degrading
  every subsequent turn's context and burning tokens. Fixed by having `save_turn()` delete all
  `turn_idx <= current` for the session (collapsing to one bucket) and having both readers select
  only the `MAX(turn_idx)` bucket.
- **`_schedule_summary_backfill()` leaks an unawaited coroutine** (symptom fixed, feature gap
  documented, NOT fully fixed): `asyncio.create_task(_backfill())` constructs the `_backfill()`
  coroutine *before* checking for a running loop; when none exists the `create_task` call raises
  (caught) but the already-constructed coroutine is never awaited, so Python eventually prints
  `RuntimeWarning: coroutine ... was never awaited`. Fixed the leak by checking
  `asyncio.get_running_loop()` first. **Not fixed:** both real entry points
  (`cli.py:734`/`api.py:92`) construct `JarvisAgent` *before* `asyncio.run()`/`uvicorn` start their
  loop, so this early-return path is hit on every real startup — meaning startup summary backfill
  (Faz 13-A) has likely never actually run outside of contexts where a loop happens to pre-exist.
  Sessions still get summarized when actively archived via `reset()` (that path runs `_schedule_summarize_one`
  from inside a running loop, so it's fine); only the "catch up on old un-summarized archives at
  startup" path is dead. Properly fixing this needs the CLI/API entry points to re-invoke backfill
  once their loop is up — deferred, tracked as **[BUG-backfill]** in the appendix, candidate for
  Faz 2 (it directly feeds the memory/summary system) or Faz 8 cleanup.

**Verify:** ✅ two concurrent `chat()`-shaped critical sections (main loop + background thread with
its own `asyncio.run()`, mirroring `TaskExecutor`) fully serialize via `_state_lock`, confirmed with
real `JarvisAgent` construction — see the ordering assertion in the Faz 0 verification scripts
(scratchpad, not committed — no test suite exists in-repo yet, see `CONTRIBUTING.md` gap).
✅ checkpoint survives a simulated process restart (fresh `make_checkpointer()` against the same
`db_path` sees the prior checkpoint) — verified with a real LangGraph `StateGraph.ainvoke()`/
`aget_state()`, not just direct saver calls. ✅ `switch_session()`/restart no longer leak or
duplicate old messages into a fresh turn — verified via `SessionStore` directly (no duplication,
old buckets collapsed, turn counter resumes above any prior thread_id) plus a concurrent
readers/writers stress test (3 writers × 3 readers, no exceptions). ✅ full `python -m jarvis`
startup smoke-tested end to end (real `Settings()` + `JarvisAgent()` construction, banner, ChromaDB
first-run download, clean EOF exit) — no exceptions from any Faz 0 code path.

## Faz 1 — Local-First Beyin + Model Router ✅ done (2026-07-14)

- [x] New `jarvis/providers/` module + `get_llm(role, settings, *, tools=, max_output_tokens=)`
      factory resolving role (realtime / reasoning / fast / local) → provider. `graph.py`'s
      hardcoded `make_llm_fast`/`make_llm_pro` (`ChatGoogleGenerativeAI` factories) are gone,
      replaced by `get_llm("fast", ...)` / `get_llm("reasoning", ...)` calls in `build_graph()`.
      The Flash/Pro keyword-pick mechanism (`agent.py`'s `_is_trivially_simple`/`use_pro_agent`,
      `nodes.py`'s `make_agent_node`) is unchanged in *logic* — it already was the complexity-based
      capacity router the plan asked for — it now just selects between local (fast) and cloud
      (reasoning) roles instead of Flash vs. Pro.
- [x] Ollama wired as a real provider: `fast`/`local`/`realtime` roles resolve to
      `ChatOpenAI` against Ollama's OpenAI-compatible endpoint
      (`settings.ollama_api_url`, model = `settings.local_model`, e.g. `qwen2.5:7b-instruct`),
      primary ahead of cloud. `nomic-embed-text` wired in `memory.py` (`_build_ollama_ef`) as the
      preferred embedding function for `jarvis_docs`/`jarvis_summaries`, ahead of the existing
      Gemini embedder, with the pre-existing EF-conflict fallback preserved so already-embedded
      collections aren't disturbed.
- [x] Cloud = escalation role: `reasoning` resolves to configured cloud tiers
      (`[Vertex Pro, AI Studio Gemini Flash]`, whichever are actually configured) — AI Studio
      targets `cloud_model_fallback` (`gemini-2.5-flash`, 1500 RPD free), deliberately NOT
      `cloud_model_pro` (`gemini-2.5-pro`, 25 RPD free) — this is the capacity-routing escalation
      target the plan asked for, not the old 429-only fallback.
- [x] VRAM budget management: decision already documented in [MEMORY.md](MEMORY.md) (Qwen2.5-7B
      Q4 + faster-whisper int8 + Kokoro-on-CPU as the target concurrent config). **No runtime
      VRAM/model-swap code was written** — there is nothing to swap yet, since voice (Faz 3) isn't
      wired to the local LLM concurrently today; a real model-swap orchestrator before that
      integration exists would be speculative code with nothing to verify. Revisit when Faz 3
      actually runs Whisper and Qwen on the GPU at the same time.
- [x] Quarantined (not deleted) `jarvis/legacy/agent_pydantic.py`'s local-model path — added a
      header note pointing at `jarvis/providers/get_llm()` as the superseding implementation.
      Confirmed via a fresh import attempt that this file is *already* broken independent of
      anything here (`ModuleNotFoundError: No module named 'pydantic_ai.models.gemini'` — a
      pydantic-ai version mismatch, pre-existing) — further evidence it's genuinely dead. Full
      deletion of `jarvis/legacy/` stays Faz 8 scope, not this phase's.
- [x] **[BUG-24]** Removed the dead `cloud_tier == "pro"` branch in `effective_cloud_model`
      (`config.py`) — `cloud_tier` is never actually set to `"pro"` anywhere.
- [x] **[BUG-22]** `switch_model()` now persists its `new_settings` onto
      `JarvisAgent._effective_settings` (new attribute); the quota-exhaustion fallback rebuild in
      `chat()` now copies `_effective_settings` instead of the construction-time `self.settings`,
      so it no longer silently discards a user's prior manual model switch.

**Bonus fixes found live while implementing/verifying the above (not in the original plan):**

- **Latent `.bind_tools()`-after-`.with_fallbacks()` crash**: the pre-Faz-1 `make_llm_fast()`
  returned `primary.with_fallbacks([fallback])` when `use_vertex=True`, and `build_graph()` then
  called `.bind_tools(tools)` on *that* — `RunnableWithFallbacks` has no `bind_tools` (confirmed:
  `hasattr(RunnableWithFallbacks, "bind_tools")` is `False`), so this would have raised
  `AttributeError` the moment anyone ran with Vertex configured. Never hit in practice only because
  recent runs have had `use_vertex=False` (missing ADC — see below). Fixed by having `get_llm()`
  bind tools *before* wrapping in `.with_fallbacks()` (pass `tools=` and it does this internally);
  `graph.py` now requests the tool-bound and bare `reasoning` variants as two separate `get_llm()`
  calls instead of binding tools onto the bare one after the fact, for the same reason.
- **Construction-time crash on a credential-less config**: `ChatGoogleGenerativeAI`'s constructor
  now validates (pydantic) that an API key is present — so the original design (always construct
  every cloud tier eagerly) raised a `ValidationError` out of `build_graph()` itself the moment
  `GEMINI_API_KEY` was empty and Vertex wasn't configured, i.e. exactly the "pure local, no cloud
  credentials at all" setup this phase is supposed to make possible. Fixed: every cloud tier is now
  built via `_safe_construct()`, which catches construction failures and drops that tier from the
  chain (logged) instead of propagating. `reasoning` also gained local Ollama as its own final
  fallback (previously it had none) — local-first means nothing should be cloud-mandatory,
  including the escalation role.
- **This dev machine initially had *zero* working tiers**: Ollama wasn't installed, Vertex ADC was
  missing, and the configured `GEMINI_API_KEY`'s AI Studio tier 429'd. **Fixed within this same
  session** (owner approved installing Ollama): `winget install Ollama.Ollama` +
  `ollama pull qwen2.5:7b-instruct` + `ollama pull nomic-embed-text`, then a clean
  `Start-Process ollama.exe serve` (the Windows installer's auto-started tray service and a first
  manual `serve` attempt raced for port 11434 and left a hung listener — killing both and starting
  one cleanly fixed it; see [MEMORY.md](MEMORY.md)). **The phase's own verify criterion is now
  fully confirmed live**: a real `JarvisAgent.chat()` turn through `build_graph()` succeeded
  end-to-end on Ollama (`qwen2.5:7b-instruct (Ollama, local)` — the correct per-turn label),
  `.bind_tools()` tool-calling on the local model was confirmed directly, and a complex-query turn
  correctly exercised the `reasoning` role. Vertex ADC is still missing (unfixed, not asked); AI
  Studio's `gemini-2.5-flash` turned out to be **intermittently rate-limited, not permanently
  dead** — repeat testing showed it both succeeding and 429ing with the same "prepayment credits
  depleted" message on different calls minutes apart.
- Also learned (informs future work touching fallbacks): `RunnableWithFallbacks.invoke()` re-raises
  the *first* tier's exception if all tiers fail, not the last — so the surfaced error after a full
  cascade failure points at the primary, not necessarily the most diagnostic tier. The router's own
  `logger.info` line (module `jarvis.providers`) records every tier that was actually configured
  for a given role — check that, not just the raised exception, when a turn fails.

**Remaining (optional, not blocking):** Vertex ADC is still missing
(`gcloud auth application-default login`) if Vertex Pro/Flash access is wanted — AI Studio Flash
and local Ollama both work without it, so this is a nice-to-have, not a gap.

**Verify:** with cloud access cut and Ollama up, `python -m jarvis` still answers; router log shows
correct role→provider; tool-calling works on the local model. **Status: ✅ fully verified live**
(Ollama installed + both models pulled this session; a real `chat()` turn answered via
`qwen2.5:7b-instruct (Ollama, local)`; tool-calling on the local model confirmed directly).

## Faz 2 — 5 Katmanlı Bilişsel Hafıza ✅ done (2026-07-14)

- [x] **Semantic:** per-turn fact extraction + consolidation/dedup; new `jarvis/fact_extractor.py`
      (mirrors `entity_extractor.py`) + new SQLite `facts` table (`jarvis/facts_store.py`) + new
      `jarvis_facts` ChromaDB collection (local-first EF chain, reusing
      `_build_embedding_function()`). Dedup happens inline at insert time via embedding-similarity
      lookup (`Memory.find_similar_fact`) — a close match bumps the existing row's
      `mention_count`/`last_seen` instead of inserting a duplicate; no separate batch
      consolidation job (kept deliberately small — see plan risk note). "Decay" is a recall-time
      ranking signal, not deletion — this is a personal memory store. **Fixed global-vs-session
      recall scoping**: `Memory.recall()` (episodic, `jarvis_memory`) now takes an optional
      `session_id` filter, and `ContextBuilder.build()` passes the current session through —
      episodic recall no longer leaks another session's raw turns. Facts (and session summaries,
      unchanged) remain deliberately cross-session — that's the entire point of those two layers.
- [x] **Procedural:** the hardcoded `_DATA_REPORT_KEYWORDS` substring match in `prompt_loader.py`
      is gone. New SQLite `procedures` table (`jarvis/procedure_store.py`) + `jarvis_procedures`
      ChromaDB collection; the pre-existing `prompts/workflows/data_report.md` is auto-seeded as
      the first row on startup (`JarvisAgent._seed_procedures_if_empty`, zero regression from
      removing the keyword trigger). Retrieval is now semantic (`Memory.recall_procedures`),
      matched each turn against the user's query. New tool `procedure_save` (#36) lets the agent
      explicitly persist a new reusable workflow after a genuinely reusable multi-tool task —
      deliberately explicit/auditable, not automatic silent capture.
- [x] **Meta:** two concrete, narrow deliverables (not the full Faz 4 `policy_guard` scope).
      (a) Runtime write-guard: `jarvis/tools/files.py`'s `write()` now refuses any path under
      `jarvis/prompts/core/` (`PermissionError`) — persona/safety directives stay
      agent-*readable* but are now provably never agent-*writable*, closing a real gap (nothing
      previously stopped `file_write` from targeting its own instructions). (b) Versioning: new
      `jarvis/prompts/CORE_VERSIONS.md` (deliberately one level *above* `jarvis/prompts/core/`,
      since `prompt_loader.py` globs every `*.md` directly inside that dir into the composed
      system prompt) tracks version/updated/note per core file, human-bumped. New `/meta` CLI
      command displays it; new `/facts` CLI command lists known facts (mirrors `/entities`).
- [x] **[BUG-25]** entity extraction (and now fact extraction) guard: `_schedule_entity_extraction`
      renamed `_schedule_memory_extraction`, gated by `JarvisAgent._should_extract` — skips only
      when *both* the user text and the response are ≤3/≤10 words (a bare "ok"/"tamam" ack),
      verified against all 3 call sites including `resume_and_stream()`'s legitimate
      `user_text=""` case (which has a real, non-trivial response and must keep firing).
- [ ] Graph-RAG — still deferred, not needed at this scale.

**Bonus finding (not in the original plan, caught during verification):** the first-pass default
distance thresholds for `recall_facts`/`recall_procedures` (0.5/0.45, modeled on `recall()`'s
existing `0.6` and `recall_summaries()`'s existing `0.55`) assumed distances in that same rough
range — but empirically, ChromaDB's default ONNX EF (the fallback whenever Ollama isn't
reachable, confirmed live-active on this dev machine during verification — not hypothetical)
produces much larger distances: ~0.07 for a near-exact paraphrase, ~0.7 for a legitimately
related but differently-worded query, ~1.7+ for something unrelated. The original thresholds
would have silently returned nothing for real, relevant recall queries under the default-EF
fallback. Fixed by recalibrating both to 1.1/1.0 based on measured distances (see the comment in
`memory.py` above `find_similar_fact`); the tight dedup threshold (`find_similar_fact`, 0.15) was
intentionally left alone — it should only ever catch near-identical phrasing.

**Verify:** ✅ all confirmed live in isolated temp dirs (never the real `data/` — see
[MEMORY.md](MEMORY.md)'s isolate-test-data-paths note), no test suite exists in-repo: a fact
stored under one session is recalled from a different session (cross-session, by design); a
near-duplicate restatement bumps the existing row instead of inserting a new one; an unrelated
fact is correctly *not* flagged as a duplicate; episodic recall scoped to session B does not
surface session A's raw turns, while scoped-to-A and un-scoped recall both still work; the seeded
`data_report` procedure is retrieved for a data/report-shaped query (no regression) and *not*
retrieved for an unrelated query; the real `procedure_save` tool (invoked directly, not just its
underlying storage calls) persists to SQLite *and* Chroma and is retrievable afterward;
`file_write` raises `PermissionError` under `jarvis/prompts/core/` while `file_read` still works
there and `file_write` still works elsewhere; the trivial-turn guard skips a bare "ok" exchange
but still fires for `resume_and_stream()`'s empty-user-text/real-response case and for
substantive exchanges; a full real `JarvisAgent()` construction (including auto-seeding,
`ContextBuilder.build()`, and system-prompt composition) succeeds end-to-end with no exceptions,
and re-construction doesn't duplicate the seed; a real `python -m jarvis` startup (against the
real, pre-existing session) loads cleanly and shuts down on EOF with no exceptions from any Faz 2
code path (the one error surfaced — Vertex ADC missing — is the same pre-existing, already-
documented gap from Faz 1, unrelated to this phase, and happened only after every Faz 2 code path
had already run cleanly).

## Faz 3 — Gerçek Zamanlı Yerel Ses ✅ done (2026-07-14)

- [x] Local streaming-cascade on the 4070: Silero-VAD (raw `.onnx` via `onnxruntime`, **not** the
      `silero-vad` pip package — hard-requires torch; pinned to **v5.1.2**, not v6.2.1, after live
      testing found v6.2.1's exported graph doesn't produce a usable speech-probability signal with
      the standard streaming calling convention despite an identical I/O shape — see
      `jarvis/voice/vad.py`'s comment before ever bumping this pin) + end-of-turn detection.
      Streaming = one-shot `faster-whisper` transcribe at VAD-detected end-of-turn (already
      installed, large-v3-turbo) stays the sole *authoritative* path — faster-whisper has no real
      incremental decode; a periodic re-transcribe for live partial captions was scoped as an
      optional stretch goal and not built (never needed to drive a turn). **Piper**, not
      Kokoro-82M, for local TTS — Kokoro doesn't support Turkish at all; Piper has `tr_TR-dfki-medium`
      + `en_US-lessac-medium` (candidate names `fahrettin`/`fettah` from early research turned out
      not to be currently published — verified against the live `rhasspy/piper-voices` `voices.json`,
      not assumed). `edge-tts` kept as a per-language fallback tier, not deleted.
- [x] Added a **binary audio channel** to `/ws` (`jarvis/ws.py` was JSON-only) — see
      `docs/VOICE_PROTOCOL.md` for the full wire spec. Always-open duplex mic
      (`jarvis/voice/io_duplex.py`'s `DuplexAudioIO`, callback-mode `sounddevice`) replaced the old
      per-turn mic + whole-reply buffer. Also built genuine **remote** transport over that same
      channel (`jarvis/voice/io_remote_ws.py`'s `RemoteWsAudioIO`, same `AudioIO` protocol as the
      local backend — no duplicated VAD/STT/TTS logic) — scope was explicitly expanded mid-phase
      (owner decision) beyond the original PC-local-only bullet, with an Electron client
      implementation (`useRemoteAudioSession` + an `AudioWorklet`) as the proof; mobile's own
      client-side capture/playback is an explicitly deferred fast-follow (new Dart deps + Android
      permission UX + real cellular jitter — materially separate effort; the protocol itself is
      already client-agnostic).
- [x] **Barge-in:** the input stream stays open during playback; sustained high-confidence speech
      (deliberately higher threshold + longer duration than normal turn-taking — a pragmatic
      mitigation for the acoustic self-bleed false-trigger risk, since this design has no true
      echo cancellation) interrupts playback and cancels the in-flight `agent.chat_stream()` task.
- [x] Turkish: relies on whisper large-v3-turbo's multilingual capability, confirmed working live
      (round-tripped Piper-synthesized Turkish speech through Whisper, correct transcript + language
      detection) — no LoRA fine-tune needed.
- [ ] Optional cloud toggle (Gemini Live as a second "realtime" role) — not built, not asked for.
- [x] **[BUG-12]** fixed — critic-revision draft+revision concatenation in streaming
      (`jarvis/graph/streaming.py`'s `graph_stream_to_text()` now tracks
      `metadata["langgraph_step"]` and inserts a separator at the pass boundary;
      `resume_and_stream()`'s independent copy-pasted duplicate now calls the shared helper).
- [x] **[BUG-13]** fixed — `chat_stream()`/`resume_and_stream()` now propagate
      `asyncio.CancelledError`/`GeneratorExit` (never swallow) while still guaranteeing
      `event_bus.state("idle")` fires, so a barge-in cancellation doesn't leave the HUD stuck.
- [x] **[BUG-23]** fixed — `openwakeword`'s `Model.reset()` now runs at the start of each listening
      session (confirmed real method — clears both `prediction_buffer` and the mel-spectrogram
      preprocessor buffer).
- [x] Bonus fixes bundled in: `--api --voice` (no `--wakeword`) previously started zero voice —
      `run_server()` now gates on `voice or wakeword`. `cli.py`'s `_collecting_stream()` no longer
      swallows `CancelledError`. `jarvis/ws.py` hardened with a per-connection writer task (JSON
      broadcasts and binary audio chunks can no longer race on one socket) and a receive-loop fix
      (a binary frame previously raised `KeyError` and silently dropped that client). Security fix
      pulled forward from the Faz 8 backlog (**`BUG-elec`**): Electron's `/ws` connection now sends
      `?token=` (reads `JARVIS_API_KEY` from the same `.env` the backend reads); the server also
      refuses `audio_session_start` outright when no API key is configured at all.

**Verify:** ✅ confirmed live: Silero VAD scores real (Piper-synthesized) speech at 0.85-0.93 mean
probability vs. silence/noise at 0.003-0.01 (both languages); Piper→Whisper round-trip produces the
expected text + correct language detection for English and Turkish; `RealtimeVoiceEngine.load()` +
`events()` run end-to-end against real local microphone/speaker hardware (ambient silence over
~1.5s correctly produces zero false transcripts/barge-ins); the full remote-audio protocol
(session claim/busy-reject/release, binary frame dispatch, unauthenticated-rejection) verified via
`starlette.testclient.TestClient` against the real FastAPI app with a real (non-mocked)
`get_shared_voice_models()` load. **Not verifiable in this environment, hand-off to the owner:**
perceived TTS/barge-in quality over real speakers (headphones test, speaker+live-interruption
test), Turkish pronunciation/prosody judgment, Electron's `getUserMedia` permission grant + real
round-trip audio over that path, end-to-end first-audio latency with real device/driver latency.

## Faz 4 — Güvenlik Çekirdeği + Asenkron Araçlar

The FULL safety kernel — prerequisite before any autonomy/physical/MCP path.

- [ ] **`policy_guard` kernel:** extract risk/confirmation logic from `make_confirmation_node`
      (`jarvis/graph/nodes.py:316-379`) into a transport-agnostic module; route the ToolNode AND
      all direct callers (voice, MCP, monitor) through it.
- [ ] **[BUG-3/4]** Wire the gate into ALL loops (today only `voice_api.py`/`/chat/confirm`):
      `cli.py` text + `cli.py`/`voice.py` voice; flip `confirmation_gate_enabled` default to True
      (`config.py:27`).
- [ ] **[BUG-6]** Per-action gating (not per-tool) so read actions (list/search) don't interrupt
      (`nodes.py:344-347`).
- [ ] **[BUG-1]** Reclassify/sandbox `python_run`/`shell_run` (L3+confirm); **[BUG-6-ssrf]** SSRF
      allow-list for `webfetch` (`tools/webfetch.py:80`); **[BUG-2]** auth on `/system/wake`
      (`api_routers/system.py:35`).
- [ ] **[BUG-5]** Remove the "never ask for confirmation" directive (`prompts/core/02_tool_policy.md`).
- [ ] Kill-switch flag + append-only audit log of every side-effecting call.
- [ ] **Async scheduler:** finally read `supports_background` in `tool_registry.py` — long tools
      (`deep_web_research`, `report_compile`, `geo_math`) run WHEN_IDLE; INTERRUPT class ties to
      barge-in. Replace the blocking `_run_coro` sub-agent bridge where feasible.
- Related debt: **[BUG-14]** unguarded `agent_node` ainvoke (`nodes.py:144`); **[BUG-recursion]**
      no `recursion_limit`/tool-call cap; **[BUG-confirm-payload]** `/chat` 500 loses
      ConfirmationRequired payload (`api.py:249`).

**Verify:** a risky tool (e.g. gmail send) prompts for confirmation in CLI + voice + API; kill-switch
off blocks physical/autonomous actions; audit log records every side effect.

## Faz 5 — MCP Katmanı

- [ ] MCP client integrated into `jarvis/graph/tools.py` tool assembly; every MCP tool gets a
      `ToolSpec` so `policy_guard` + the scheduler cover it.
- [ ] Keep the ~34 existing `@tool` wrappers as-is (dual layer); MCP tools inherit the same
      gate + audit.

**Verify:** an external MCP server's tools appear as gated tools; no MCP tool bypasses `policy_guard`.

## Faz 6 — Fiziksel Dünya / IoT  ⛔ hardware-gated (deferred)

Needs Faz 4 (safety) + Faz 5 (ha-mcp). Owner has only an RP2040 today.

- [ ] Stand up Home Assistant in a container/VM on the PC (no purchase) + Mosquitto + Zigbee2MQTT
      (**requires a Zigbee coordinator dongle**) + starter devices.
- [ ] Connect via ha-mcp (Faz 5); register HA actuation tools at L3+`requires_confirmation=True`;
      wire the kill-switch.
- [ ] Staged: read-only (sensor state) → reversible actuation (lights/plugs) → much later
      locks/climate; each behind the gate + audit log.
- [ ] RP2040 (if Pico W) could become an MQTT sensor node later.
- Matter/Thread deferred (border-router dependency); Zigbee2MQTT is enough to start.

## Faz 7 — Proaktiflik (event-driven self-initiation)  ⛔ partly deferred, highest risk

Needs the thread-safe agent (Faz 0), durable facts (Faz 2), and — for sensor events — MQTT (Faz 6).

- [ ] Give `monitor.py` a path INTO `agent.chat()` (today toast/FCM only) via the Faz 0 serialized queue.
- [ ] Software-proactivity (calendar/email self-initiation) can start without hardware; sensor
      proactivity waits for Faz 6.
- [ ] MQTT event subscriber → event bus → policy-gated autonomous action; rate-limit + dedup
      (reuse the `_notified_*` pattern in `monitor.py:41-43`) to prevent runaway loops.
- [ ] **[BUG-19]** budget/GCP alerts have no dedup and re-fire every poll cycle (`monitor.py:390`)
      — fold into the same dedup work.
- All autonomous side-effects pass `policy_guard` + audit + kill-switch; physical actions default
      to confirm-or-notify, not silent execution.

## Faz 8 — Temizlik & Konsolidasyon

- [ ] Retire `jarvis/legacy/`; delete the dead `jarvis/prompts/system.md` pointer file.
- [ ] Clean up the 21 `.claude/worktrees/*` scratch branches (owner go-ahead — destructive).
- [ ] Merge `langgraph-migration` → `main`; add a minimal test suite (none exists today);
      offline-failover tests.
- Note: offline resilience is largely already achieved by the local-first Faz 1-3 work.
- Remaining P2 polish bugs not yet slotted: **[BUG-20]** `file_write` ValueError on home paths
      (`tools/files.py:56`); **[BUG-21]** calendar dedup hardcoded `+03:00` (`tools/calendar.py:261`);
      **[BUG-itu]** IMAP connection leak on login failure (`tools/itu_mail.py:61`); **[BUG-pdf]**
      pdf cache keyed on path not content (`tools/pdf.py:54`); **[BUG-geomath]** Devito fallback
      hardcodes `duration=0.5` (`geo_math_tool.py:197`); **[BUG-modelswitch]** unanchored "pro"
      substring hijack (`cli.py:116`); **[BUG-emptyresp]** empty LLM response saved as success
      (`nodes.py:215`); **[BUG-upload]** `/chat/upload` no size cap + no cleanup (`api.py:391`);
      **[BUG-usage]** `UsageTracker` clobbers across processes (`usage.py:63`); electron/mobile
      client hygiene (no API-key header, cleartext ws token, no reconnect backoff).

---

## Bug backlog appendix (59-finding review, 2026-07-14)

Every finding from the review, mapped to the phase that owns it. The 15 highest-severity were
also reported via this session's code-review tooling.

| Tag | File:line | Summary | Phase |
|---|---|---|---|
| BUG-1 | graph/tools.py:132 | `python_run` unsandboxed, arbitrary abs paths, L2/no-confirm | 4 |
| BUG-2 | api_routers/system.py:35 | `/system/wake` unauthenticated | 4 |
| BUG-3 | cli.py:606 | CLI text REPL never handles `ConfirmationRequired` → hangs | 4 |
| BUG-4 | agent.py:652 | confirm interrupt spoken as raw JSON in voice/HUD | 4 |
| BUG-5 | prompts/core/02_tool_policy.md:23 | "never ask before any tool" directive | 4 |
| BUG-6 | graph/nodes.py:344 | gating per-tool not per-action | 4 |
| BUG-6-ssrf | tools/webfetch.py:80 | no SSRF/private-range guard | 4 |
| BUG-8 | agent.py:481 | shared agent singleton mutated with no locking | 0 ✅ |
| BUG-9 | graph/graph.py:98 | checkpointer silently in-memory, ignores db_path | 0 ✅ |
| BUG-10 | session_store.py:186 | read/write lock asymmetry + non-transactional save_turn | 0 ✅ |
| BUG-11 | agent.py:333 | switch_session thread_id collision corrupts history | 0 ✅ |
| BUG-dup | session_store.py:216 | save_turn/load_history duplicate messages (cumulative snapshot read as delta) | 0 ✅ |
| BUG-backfill | agent.py:413 | summary backfill leaks unawaited coroutine; never actually runs at startup | 0 (leak fixed; feature gap deferred) |
| BUG-12 | graph/streaming.py:26 | critic draft+revision concatenated, persisted | 3 ✅ |
| BUG-13 | agent.py:636 | chat_stream only catches GraphInterrupt, drops turn | 3 ✅ |
| BUG-14 | graph/nodes.py:144 | agent_node ainvoke unguarded | 4 |
| BUG-recursion | graph/state.py:11 | no recursion_limit / tool-call cap | 4 |
| BUG-confirm-payload | api.py:249 | `/chat` 500 loses ConfirmationRequired payload | 4 |
| BUG-15 | tools/finance.py:161 | finance sync regex never matches → feature dead | 0 |
| BUG-16 | graph/tools.py:670 | todo('add') bg analysis silently fails | 0 |
| BUG-17 | gcp_quota.py:106 | quota cache key never hits | 0 |
| BUG-18 | gcp_quota.py:307 | forecast daily-rate math wrong → false alarms | 0 |
| BUG-19 | monitor.py:390 | budget/quota alerts no dedup, re-fire every cycle | 7 |
| BUG-20 | tools/files.py:56 | file_write uncaught ValueError on home paths | 8 |
| BUG-21 | tools/calendar.py:261 | dedup guard hardcodes +03:00 | 8 |
| BUG-itu | tools/itu_mail.py:61 | IMAP connection leak on login failure | 8 |
| BUG-pdf | tools/pdf.py:54 | pdf cache keyed on path not content | 8 |
| BUG-geomath | tools/geo_math_tool.py:197 | Devito fallback hardcodes duration=0.5 | 8 |
| BUG-modelswitch | cli.py:116 | unanchored "pro" substring hijacks messages | 8 |
| BUG-22 | agent.py:514 | quota fallback ignores switch_model, stale label | 1 ✅ |
| BUG-emptyresp | graph/nodes.py:215 | empty LLM response saved as success | 8 |
| BUG-24 | config.py:125 | effective_cloud_model "pro" branch dead code | 1 ✅ |
| BUG-upload | api.py:391 | /chat/upload no size cap, no cleanup | 8 |
| BUG-23 | voice.py:138 | openwakeword buffer never reset between sessions | 3 ✅ |
| BUG-usage | usage.py:63 | UsageTracker clobbers across concurrent processes | 8 |
| BUG-25 | agent.py:552 | entity extraction fires every trivial turn | 2 |
| BUG-elec | electron/App.jsx | HUD never sends API-key header | 8 |
| BUG-mob-tls | mobile/ws_client.dart:22 | cleartext ws token in query string | 8 |
| BUG-reconnect | ws_client.dart:42 | 3s reconnect forever, no backoff cap | 8 |

## Previously-completed refactor phases (context)

| Phase | What | Status |
|---|---|---|
| 1 | System prompt modularization | ✅ 2026-05-24 |
| 2 | ToolSpec risk metadata | ✅ 2026-05-24 |
| 3 | LangGraph confirmation-gate node | ✅ shipped, not functional end-to-end → Faz 4 |
| 4 | Memory-retrieval extraction into `ContextBuilder` | ✅ 2026-05-24 |

The old refactor "Phase 5-8" (TaskExecutor persistence, monitor-in-api, voice barge-in, sub-agent
migration) are now subsumed into the phases above (barge-in → Faz 3, monitor→chat → Faz 7, etc.).
