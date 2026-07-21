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
| 4 | Güvenlik çekirdeği + async araç | Otonomi/MCP/IoT ön koşulu | L | ✅ done (2026-07-14) |
| 5 | MCP katmanı | IoT yazılım ön koşulu | M | ✅ done (2026-07-15) |
| 6 | Fiziksel dünya / IoT | ⛔ Donanıma bağlı (Faz 0+4+5) | L + HW | ⬜ deferred |
| 7 | Proaktiflik | Capstone; kısmen donanıma bağlı | L | 🟡 software half done (2026-07-15), sensor half deferred (Faz 6) |
| 8 | Temizlik & konsolidasyon | — | M | 🟡 non-destructive scope + merge to main done (2026-07-15), only worktree-cleanup deferred |

Faz 1+2 = "hafıza + zeka" ilk bloğu (ikisi de yerel).

---

## What's actually next: Agent Runtime rev.2 (started 2026-07-20)

The table above (this file's original local-first plan) is now done or hardware-gated (Faz 6
IoT is the only real remainder, blocked on hardware the owner doesn't have yet). The genuinely
forward-looking work as of 2026-07-20 is a **separate** plan, kicked off after a dev-focused
review of tool-calling reliability (context leakage, wrong tool args, fabricated success
claims, run-to-run variance) — an execution-contract runtime so JARVIS uses *any* tool
reliably, not new domain tools. Full plan (9 phases, **also numbered Faz 0-8 — do not confuse
with the table above**, always qualified as "Agent Runtime rev.2" in code/docs):
`C:\Users\mertk\.claude\plans\c-users-mertk-desktop-gpt-analysis-md-s-delegated-scone.md`.
Status: Faz 0-6(Part 1) are done and committed as of 2026-07-22 (Faz 0-3 pushed as `f4b7609`;
Faz 4 — verified response composition — committed locally as `9c0ca15`; Faz 5 — isolation &
reproducibility — committed locally as `5eae027`; Faz 6 Part 1 — typed schema definitions —
committed locally as `cb2b1a2`; none of the three pushed yet). Faz 6 ("typed schemas + bounded
repair") Part 1 — real pydantic `args_schema` for the plan's 12 named-priority tools
(`plot_data` + 11 action-dispatch tools), wired onto `TOOL_SPECS`, plus an
`[INVALID_ARGS:<field>]` reason-code parser. **Deliberately not done in Part 1** (see
[HANDOFF.md](HANDOFF.md) for why, Part 2 is next): no live
`@tool` function signature was changed and nothing validates against these schemas yet — that
wiring, plus the bounded-repair pipeline itself (still needs a concrete design, not just the
plan's vocabulary), is Part 2. 841 pytest green (792+49 new), ruff clean. See
[MEMORY.md](MEMORY.md)'s own "Agent Runtime rev.2" section and [HANDOFF.md](HANDOFF.md) for the
current detail.

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
- **`_schedule_summary_backfill()` leaks an unawaited coroutine** (leak fixed same session; the
  feature-gap half — **fixed 2026-07-15, GPT-5.6 review remediation Faz 7**): `asyncio.create_task
  (_backfill())` constructs the `_backfill()` coroutine *before* checking for a running loop; when
  none exists the `create_task` call raises (caught) but the already-constructed coroutine is never
  awaited, so Python eventually prints `RuntimeWarning: coroutine ... was never awaited`. Fixed the
  leak by checking `asyncio.get_running_loop()` first. The "not fixed" half — both real entry points
  construct `JarvisAgent` *before* `asyncio.run()`/`uvicorn` start their loop, so the early-return
  path was hit on every real startup, meaning startup summary backfill (Faz 13-A) never actually ran
  — is now fixed: `JarvisAgent.run_startup_backfill()` is called again from `cli.py`'s
  `_run_loop()`/`_run_voice_loop()` and `api.py`'s `lifespan()`, once their loop is genuinely up
  (idempotent, safe to call more than once). See CHANGELOG.md's "[GPT-5.6 review remediation]" entry
  and `tests/test_startup_backfill.py`.

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

## Faz 4 — Güvenlik Çekirdeği + Asenkron Araçlar ✅ done (2026-07-14)

The FULL safety kernel — prerequisite before any autonomy/physical/MCP path.

- [x] **`policy_guard` kernel:** new `jarvis/policy_guard.py` — transport-agnostic, no
      LangGraph/LangChain imports. Extracted the risk/confirmation logic out of
      `make_confirmation_node` (`jarvis/graph/nodes.py`, which now calls into it) so the ToolNode
      path and any future direct caller (MCP — Faz 5; monitor.py currently makes zero tool calls,
      so nothing to route yet) share exactly one place that answers "is this allowed, does it need
      the user's OK." Also owns the kill-switch veto check.
- [x] **[BUG-3/4]** Gate wired into all three interfaces, not just `/chat/confirm`:
      `cli.py`'s text REPL catches `ConfirmationRequired` and prompts y/n (+ optional reason).
      Voice (`cli.py`'s `--voice` loop, `voice_api.py`'s wakeword/PTT loop, and `api.py`'s `/ws`
      remote-audio session — all three, via shared helpers in new `jarvis/voice/session.py`)
      detect the `__jarvis_confirm__` marker instead of speaking it as raw JSON, speak a natural
      question, and treat the next utterance as the yes/no answer. `confirmation_gate_enabled`
      now defaults `True` (`config.py`).
- [x] **[BUG-6]** Per-action gating: `policy_guard._READ_ACTIONS` downgrades list/search/read/
      download actions on `google_calendar`/`gmail`/`google_drive`/`itu_mail` back to L1/no-confirm
      — only genuinely risky actions on those four tools interrupt.
- [x] **[BUG-1]** `python_run` reclassified L2→L3 + `requires_confirmation=True` (matches
      `shell_run`'s existing L3/deny-listed/gated treatment). **Not done: actual sandboxing**
      (subprocess resource/network restriction) — this is the access-control fix, not a sandbox;
      documented as a deferred hardening item in `docs/SAFETY.md`, not silently claimed.
      **[BUG-6-ssrf]** `webfetch.py`'s `fetch_url()` now refuses localhost/private/link-local/
      reserved/metadata-endpoint URLs, checked against the *resolved* IP (DNS-rebinding-safe), not
      just the hostname string. **[BUG-2]** `/system/wake` now requires `X-API-Key` like every
      other mobile router (new shared `jarvis/api_auth.py`); `/system/ping` stays auth-free by
      design.
- [x] **[BUG-5]** `prompts/core/02_tool_policy.md`'s "you do NOT need to ask" directive replaced
      with accurate guidance: don't pre-emptively ask, but DO expect (and correctly react to) the
      system's own approve/deny round-trip on risky calls.
- [x] Kill-switch: new `jarvis/kill_switch.py`, persisted to `data/kill_switch.json` (survives a
      restart — a trip stays tripped until deliberately re-armed), default enabled. Scoped to L3
      (external-effect) actions only — vetoes at the same point `policy_guard` would otherwise
      offer a confirmation prompt, skipping the prompt entirely (asking is pointless once the
      operator already said stop). `/killswitch [status|on|off <reason>]` in the CLI.
      Append-only audit log: new `jarvis/audit_log.py`, `data/audit_log.jsonl`, two event kinds —
      `decision` (policy_guard's ruling, written in `confirmation_node`) and `execution_start`/
      `execution_end` (the call actually ran + outcome, written by `agent.py`'s `_HudEventCallback`
      — the same LangChain callback attached for every transport, so this isn't CLI/API-specific).
- [x] **Async scheduler:** `task_executor.py`'s `ASYNC_KEYWORDS` is now genuinely derived from
      `TOOL_SPECS[...].supports_background` (union with the pre-existing hand-picked phrase set, so
      this is a strict superset — no regression). The five sub-agent bridges (`math_solve`,
      `write_content`, `research`, `generate_code`, `geo_math`'s analyze branch) and `todo` were
      converted from sync `@tool def` + the `_run_coro()` thread-and-fresh-event-loop bridge to
      native `async def` `@tool`s — `_run_coro()` itself is now dead code and was deleted (nothing
      else in `graph/tools.py` called it; `tools/finance.py` has its own separate, untouched
      `_run_coro`). Voice loops (`voice_api.py`'s `run_one_response`, used by both the local
      wakeword/PTT loop and the remote `/ws` session — **not** the standalone CLI `--voice` mode,
      which has no `TaskExecutor` attached) now hand a `should_async()`-flagged query to
      `TaskExecutor` with a short spoken acknowledgement instead of blocking the turn (and the mic)
      in silence for up to minutes; completion fires a Windows toast in addition to the pre-existing
      FCM push. **Not built: a formal WHEN_IDLE/INTERRUPT work-class taxonomy** — deliberately kept
      to the existing run-now-vs-TaskExecutor granularity (whole-turn, not per-tool-call
      mid-reasoning) since a finer-grained scheduler has no second consumer yet and per-tool-call
      backgrounding would break the ReAct loop for turns that need the tool's result to answer.
- [x] Related debt: **[BUG-14]** `agent_node`'s `llm.ainvoke()` now wrapped in
      `asyncio.wait_for(..., timeout=settings.agent_llm_timeout_sec)` — a wedged provider surfaces
      a clear in-conversation error instead of hanging the turn forever. **[BUG-recursion]** new
      `Settings.graph_recursion_limit` (default 30) passed as LangGraph's `recursion_limit` in every
      `chat()`/`chat_stream()` config. **[BUG-confirm-payload]** `/chat` now catches
      `ConfirmationRequired` before the generic exception handler and returns
      `{"confirmation_required": true, "id", "payload"}` instead of an opaque 500.

**Bonus finding (not in the original plan):** `TaskExecutor._run()`'s background `agent.chat()`
call could raise `ConfirmationRequired` (it's an `Exception` subclass) with no channel to answer
it — previously would have surfaced as a cryptic `"confirmation_required:<uuid>"` failure message.
Now caught specifically and reworded to name the blocked action and tell the user to ask
interactively instead.

**Explicitly deferred, not this phase's scope (see `docs/SAFETY.md`'s "Known limits"):** no
Electron/mobile UI renders a confirmation prompt yet (API returns the right structured payload;
nothing consumes it) — a real, unstarted UI task, not a tooling gap: Node.js was installed
2026-07-15, right after this phase, and `npm run build` passes clean across every Faz 3 JS/JSX
file (see [MEMORY.md](MEMORY.md)/[HANDOFF.md](HANDOFF.md)). `python_run` is gated, not
sandboxed. Voice confirmation's per-call description stays in English technical form even in a
Turkish session (only the wrapper question is bilingual).

**Verify:** ✅ 61 isolated checks (`policy_guard` per-action decisions including all four mixed-risk
tools' read/write split, kill-switch veto scoped to L3 only, `python_run`'s reclassification,
`audit_log` writes/truncation/tail, the SSRF guard against 7 blocked address classes plus a real
public URL allowed through, `voice/session.py`'s marker-parsing/affirmative-detection/bilingual
question text, `task_executor`'s registry-derived keywords, `Settings` defaults, `recursion_limit`
present in both `chat()`/`chat_stream()` configs, `confirmation_node`'s source referencing
`policy_guard`/`audit_log`/the kill-switch veto path) — scratchpad, not committed, no test suite
exists in-repo yet. ✅ A second isolated check drove a REAL (unmocked) LangGraph compiled graph +
LangChain callback manager through `_HudEventCallback` end-to-end (a fake `AIMessage` with
`tool_calls` stands in for the model's decision, so no LLM/credentials needed) — confirmed
`on_tool_start`/`on_tool_end` really do receive `run_id` from LangChain's real callback manager and
the resulting `execution_start`/`execution_end` audit pair actually lands, for a real registered L2
tool name. ✅ Full `python -m jarvis` startup smoke-tested end to end against real (not isolated)
session data — confirmed a real turn now flows through the new `confirmation_gate_enabled=True`
default, the new `recursion_limit`-bearing graph config, and the rebuilt `confirmation_node` without
any new exception; it stopped at this dev machine's pre-existing, already-documented Vertex-ADC gap
(unrelated to this phase, same finding as Faz 1) and shut down cleanly on EOF. **Not verifiable in
this environment:** a live tool-calling turn through a real LLM (Ollama wasn't running this
session) — closed via the real-graph/fake-AIMessage test above instead, which exercises the same
LangChain callback machinery without needing one. Perceived voice-confirmation UX (actually hearing
the spoken question and answering by voice) — same hand-off category as Faz 3's unverifiable
speaker/mic items.

## Faz 5 — MCP Katmanı ✅ done (2026-07-15)

- [x] MCP client integrated: new `jarvis/mcp_integration.py` (`McpToolManager`, built on the
      official `langchain-mcp-adapters`). Every discovered MCP tool gets a `ToolSpec` synthesized
      at connect time via `tool_registry.register_dynamic_spec()`, inserted into the exact same
      `TOOL_SPECS` dict the 36 native tools live in — `policy_guard`, `audit_log`, and the async
      scheduler cover MCP tools with **zero code changes** to any of them (they only ever call
      `get_spec()`/read `TOOL_SPECS`). `jarvis/graph/graph.py`'s `build_graph()` gained an
      `extra_tools` param that `jarvis/graph/tools.py`'s native `make_tools()` list is merged with.
- [x] Kept the 36 existing `@tool` wrappers completely as-is (dual layer, confirmed by test: a
      graph built with `extra_tools=None` has zero `browser_*` tools — no regression). MCP tools
      inherit the identical confirm gate + audit trail.
- [x] Config-driven, extensible to a future server (ha-mcp, Faz 6) with zero new code:
      `Settings.mcp_servers` (generic JSON `{name: {command, args, transport}}`, the exact shape
      `MultiServerMCPClient` itself takes) + dedicated `mcp_playwright_enabled`/
      `mcp_playwright_headless` convenience flags for the one server shipped this phase
      (Microsoft's official Playwright MCP — real browser automation: navigate/click/type/
      snapshot/screenshot/evaluate JS/…, ships **disabled by default**).
- [x] Fail-closed classification (the phase's own risk callout: "no MCP tool may bypass the
      gate"): a short explicit allow-list of pure-inspection/inconsequential-navigation Playwright
      tool names gets L1/L2 no-confirm; **every other tool — including any name never seen before**
      — defaults to L3 + `requires_confirmation=True`, same gate as `shell_run`/`gmail send`. This
      is the concrete mitigation for the prompt-injection risk a browser tool uniquely adds beyond
      `web_search`/`url_read` (already-untrusted page text) — a poisoned page can make the model
      *want* to click/submit something, but can't act without the user approving that exact call.
- [x] Solved the actual hard part of this phase (not called out in the original 2-bullet scope, a
      real correctness issue found during implementation, not assumed from docs): MCP's stdio
      transport needs ONE persistent subprocess for a session's life for a *stateful* server like
      browser automation (confirmed live — the adapter's default stateless `get_tools()` spawns a
      fresh process, and fresh blank browser, per tool call, silently breaking `navigate` →
      `click`). `McpToolManager` uses the persistent `client.session()` pattern instead. That
      session is loop-bound (same class of constraint as the checkpointer, see `graph/graph.py`'s
      docstring) — `JarvisAgent.connect_mcp_tools()` is therefore called explicitly, once, from
      the real long-lived loop in each entry point (`cli.py`'s `_run_loop`/`_run_voice_loop`,
      `api.py`'s `lifespan()`) before any turn or `TaskExecutor` background job can run, never
      lazily from whichever caller happens to `chat()` first.
- [x] Windows gotcha fixed (confirmed live, not assumed): `npx` is `npx.cmd`, a batch shim —
      spawning it directly raises `WinError 2`. Every npx-based server config goes through
      `cmd /c npx ...`.

**Verify:** ✅ core mechanism fully confirmed live (2026-07-15), see [MEMORY.md](MEMORY.md) for the
full design write-up and [HANDOFF.md](HANDOFF.md) for the live-turn detail. Three tiers:
(1) **Isolated, 26/26 checks** (temp cwd, per this project's isolate-test-data-paths lesson —
`kill_switch.py` also persists to a cwd-relative file, not just `SessionStore`/`Memory`, learned
while writing this phase's verification): real Playwright MCP tools discovered, every one gets a
`ToolSpec`, fail-closed classification correct for both known-safe and known-risky tool names *and*
an unseen future name, kill switch vetoes a tripped L3 MCP call but correctly does not veto an L1
one, no MCP tool is background-eligible. (2) **Graph-level, 6/6 checks**: a compiled graph built
without MCP tools has zero `browser_*` entries (no regression); the MCP-merged graph's actual
`ToolNode` — not a mock — both contains them and genuinely dispatches through the persistent
session (`browser_navigate` then a separate `browser_snapshot` call see the same page).
(3) **Real product, live LLM** (`python -m jarvis`, real Gemini 2.5 Pro via Vertex, not simulated):
confirmed via `data/audit_log.jsonl` — the model independently decided to call `browser_navigate`
(logged `auto_approved`, executed, real page content came back) and, separately, `browser_click`
(logged `risk_level: 3, outcome: confirm_required` — the fail-closed gate firing for real, from a
real model's decision, not a synthetic test). This same live run caught a real bug, now fixed (see
below) — a good example of why this tier is worth the friction even when scripted checks pass.
**Not cleanly closed:** the CLI's interactive approve/deny round-trip specifically over piped stdin
produced an empty final response instead of a rendered confirmation prompt on both live attempts —
`agent.chat()`'s `GraphInterrupt`→`ConfirmationRequired` handling and `confirmation_node`'s
interrupt logic both read correctly on inspection and are unmodified Faz 4 code, so this looks like
piped-non-TTY-stdin racing multiple sequential `Prompt.ask()` calls rather than a gating bug — but
it's not proven either way. Hand-off: re-run the same request from a real interactive terminal
(not piped) to confirm the prompt renders and an approval actually resumes the click.

**Bug caught live, not assumed (fixed same session):** `JarvisAgent.connect_mcp_tools()`'s graph
rebuild was gated on `if self._mcp.tools:` alone — true forever after the first successful connect,
so every call after the first (including the belt-and-suspenders one at the top of every single
`chat()`/`chat_stream()`) silently rebuilt the *entire* graph — re-constructing every LLM provider
and re-running `.bind_tools()` across all ~60 tools — on every turn for the rest of the process's
life. Caught by the live smoke test's repeated `bind_tools()` schema-warning volume, not by either
isolated script (neither exercises `JarvisAgent` itself, per the isolate-test-data-paths
constraint). Fixed with a one-time `self._mcp_graph_rebuilt` guard, reset on `close_mcp_tools()`.

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

## Faz 7 — Proaktiflik (event-driven self-initiation)  🟡 software half done (2026-07-15), sensor half deferred

Needs the thread-safe agent (Faz 0), durable facts (Faz 2), and — for sensor events — MQTT (Faz 6).

- [x] Give `monitor.py` a path INTO `agent.chat()` (today toast/FCM only) via the Faz 0 serialized
      queue. New `JarvisAgent.proactive_turn()` (`jarvis/agent.py`) — takes `_state_lock` exactly
      like `chat()`/`chat_stream()`, runs the same compiled graph (same tools, same
      `policy_guard`/kill-switch/audit_log gate, zero new gating code), but on an isolated message
      list + dedicated LangGraph thread_id — never touches `self._history`/`_turn`/
      `session_store.save_turn` or episodic memory, so JARVIS's internal "should I say anything?"
      self-talk never leaks into the user's real conversation history. `JarvisMonitor` takes an
      optional `agent=` reference (wired from both `cli.py` and, new this phase, `api.py`'s
      `lifespan()` — see the "monitor-in-api" bullet below) and calls it via `asyncio.run()` from
      its own daemon thread, the same bridging pattern `TaskExecutor._run()` already uses.
- [x] Software-proactivity (calendar/email self-initiation) — new `monitor.py`'s `_maybe_proactive()`,
      called from `_check_email()`/`_check_calendar()` alongside the existing unconditional toast.
      Off by default (`Settings.monitor_proactive_enabled=False`) — existing toast/FCM-only behavior
      is completely unaffected until explicitly enabled. Throttled
      (`monitor_proactive_min_gap_sec`, default 600s) across all sources combined, so a burst of
      unread emails after being offline can't queue a pile of LLM calls. Sensor proactivity still
      waits for Faz 6 (no hardware).
- [x] **Confirm-or-notify, not silent execution**: `proactive_turn()` never raises
      `ConfirmationRequired` (no interactive channel exists for a background thread to answer it —
      same constraint `TaskExecutor` already hits). If the graph interrupts for an L3 action, the
      pending confirmation is discarded (never resumed, never silently executed) and reported back
      as `kind="needs_confirmation"`; `monitor.py` turns that into a toast/push naming the gated
      tool(s) and telling the user to ask JARVIS directly. This was a deliberate scope decision, not
      a shortcut: the WS `confirmation_required` event + `POST /chat/confirm/{conf_id}` plumbing
      exists, but per `docs/SAFETY.md`'s Known limits, no UI actually consumes it yet — leaving a
      proactively-raised confirmation "pending forever" behind that dead end would be worse than
      naming it and pointing the user at the interactive path that does work.
- [x] **[monitor-in-api]** `--monitor` was previously silently ignored in `--api` mode (only
      `cli.py`'s branch ever constructed a `JarvisMonitor` — see `docs/ARCHITECTURE.md`'s old "Known
      gaps" table). `api.py`'s `lifespan()` now starts one when `run_server(..., monitor=True)`,
      matching the `--voice`/`--wakeword` wiring shape; `__main__.py`'s `--api` branch now threads
      `args.monitor` through. This is where proactive monitoring matters most in practice — an
      always-on backend, not just an interactive CLI session left open.
- [ ] MQTT event subscriber → event bus → policy-gated autonomous action — still blocked on Faz 6
      hardware (no Zigbee coordinator dongle, no Home Assistant instance).
- [x] **[BUG-19]** budget/GCP alerts had no dedup and re-fired every poll cycle (`monitor.py:390`).
      Fixed: `gcp_quota.quota_alert_check()` now returns `(alert_key, message)` pairs (the message
      text embeds live numbers that change every call, so text-based dedup wouldn't work);
      `_check_gcp_quota()` dedups per day (a recurring daily signal — permanently suppressing after
      the first alert would hide a real problem on day 2), `_check_finance()`'s budget-threshold
      loop dedups per `(year, month, category)` (naturally self-clears at the start of each new
      month, matching how a monthly budget actually resets).
- [x] All autonomous side-effects pass `policy_guard` + audit + kill-switch (for free — `
      proactive_turn()` runs the exact same compiled graph `chat()` does); physical actions default
      to confirm-or-notify, not silent execution (see above). No physical/MQTT actions exist yet to
      actually exercise this end of the constraint — enforced today for the email/calendar triggers
      this phase actually ships.

**Verify:** see [HANDOFF.md](HANDOFF.md) for the full write-up. Isolated stub-agent tier (15/15
checks): agent=None and `monitor_proactive_enabled=False` are both true no-ops (zero behavior
change to existing toast-only monitoring); enabled+`kind=none` calls the agent but stays silent;
enabled+`kind=response`/`needs_confirmation` fire the correct distinct toast; throttle blocks a
second call inside the gap window and allows one after it elapses; GCP/budget dedup each collapse
3 synthetic poll cycles into exactly 1 toast. Real-`JarvisAgent` tier (isolated temp cwd, per
MEMORY.md's isolate-test-data-paths lesson): a deterministically-mocked `GraphInterrupt` is caught
and reported as `needs_confirmation` with the pending tool name, the pending confirmation is NOT
left in `_pending_confirmations` (nothing will ever resume it), and `_state_lock` is released, not
deadlocked; a real local-LLM (`qwen2.5:7b-instruct` via Ollama) proactive turn against a mundane
calendar trigger completes without touching `self._history`/`_turn`/`session_store`'s saved turn
index; a real local-LLM turn given an explicit gated-action instruction was observed to correctly
interrupt via the real graph + real `policy_guard`, not just a mocked path.

## Faz 8 — Temizlik & Konsolidasyon 🟡 non-destructive scope + merge to main done (2026-07-15), only worktree-cleanup deferred

- [x] Retire `jarvis/legacy/`; delete the dead `jarvis/prompts/system.md` pointer file.
      Done (2026-07-15) — confirmed via grep first (per `CLAUDE.md`'s standing warning) that
      nothing outside `jarvis/legacy/` itself imported it; both `git rm -r`'d outright, not
      archived. Old pydantic-ai orchestrator is recoverable from git history before this commit
      if ever needed for reference.
- [ ] Clean up the 21 `.claude/worktrees/*` scratch branches (owner go-ahead — destructive).
      Still deferred, unchanged from prior sessions.
- [x] Merge `langgraph-migration` → `main` — done 2026-07-15, same-day follow-up session, after
      explicit owner go-ahead. Pure fast-forward (`main` had zero commits of its own — it was frozen
      at a 2026-05-09 baseline, 67 commits behind). Repo also pushed to a new GitHub remote
      (`origin` → `mertkaanakgunlu-debug/JARVIS`, public) — see the follow-up write-up below.
- [x] Add a minimal test suite (none existed before this phase) + offline-failover tests.
      New `tests/` — pytest + pytest-asyncio (added to `requirements.txt`), configured via
      `[tool.pytest.ini_options]` in `pyproject.toml` (`testpaths = ["tests"]`,
      `asyncio_mode = "auto"`). 92 tests across 10 files: `policy_guard` (risk classification,
      the BUG-6 per-action read/write downgrade, kill-switch veto scoping), `session_store`
      (no-duplication across turn buckets, `last_turn_idx` resumption, rollback-on-failure
      atomicity, a concurrent-writers-and-readers stress test), `kill_switch`/`usage.py` (the two
      cross-process bugs fixed this session — see below), the provider router
      (`jarvis/providers/get_llm()` — **this is the offline-failover coverage**: with no cloud
      credentials configured at all, both the `fast` and `reasoning` roles resolve to a bare
      local Ollama model, not a fallback wrapper around nothing; with cloud configured, local
      Ollama is confirmed always last in the `reasoning` chain), and one regression test file per
      bug fixed below. New `tests/conftest.py`'s `isolated_cwd` fixture is the enforcement point
      for MEMORY.md's isolate-test-data-paths lesson — see its docstring. Not exhaustive (most
      tool modules still have zero coverage) — "minimal," per the phase's own scope, not a full
      suite. See `CLAUDE.md`/`CONTRIBUTING.md` for how to run it (`pytest` from repo root).
- [x] **[BUG-20]** `file_write` ValueError on home paths (`tools/files.py`) — the return message
      unconditionally did `p.relative_to(workspace)`, which raises for any path `_resolve()`
      legitimately allowed under the home directory but outside `workspace`. New `_display_path()`
      helper falls back to home-relative, then absolute.
- [x] **[BUG-21]** calendar dedup hardcoded `+03:00` (`tools/calendar.py`) — the create-event dedup
      window built its `timeMin`/`timeMax` by string-appending a literal `+03:00` instead of using
      `settings.calendar_timezone`. Correct only by coincidence for the default Europe/Istanbul
      (no DST since 2016); silently wrong for any other configured zone. Now uses `zoneinfo` to
      reinterpret the naive wall-clock values with the actual configured tz (DST-correct).
- [x] **[BUG-itu]** IMAP connection leak on login failure (`tools/itu_mail.py`) — `MailBox(host,
      port)`'s constructor opens the socket/SSL handshake immediately; if the subsequent
      `.login()` raised, that connection was never closed (the `with` statement in every call site
      never got a chance to start). Now wraps `.login()` in try/except and calls `.logout()` on
      failure before re-raising.
- [x] **[BUG-pdf]** pdf cache keyed on path not content (`tools/pdf.py`) — the cache key hashed the
      resolved path string, relying entirely on mtime to detect a changed file at the same path.
      mtime is not reliable (a restored backup, `cp -p`, or an archive extraction can leave newer
      content with an *older* mtime than a stale cached conversion). New `_content_key()` hashes
      the file's actual bytes; a changed file gets a different cache key regardless of mtime, and
      the mtime-based freshness check (`_is_cache_fresh`) is gone entirely — no longer needed once
      the key itself is content-derived.
- [x] **[BUG-geomath]** Devito fallback hardcodes `duration=0.5` (`tools/geo_math_tool.py`) — when
      Devito is installed but fails at *runtime* (not just "not installed"), the except-branch's
      call into the NumPy FDM fallback hardcoded `duration=0.5` instead of passing through the
      caller's actual requested duration, silently truncating any simulation the user asked for.
- [x] **[BUG-modelswitch]** unanchored "pro" substring hijack (`cli.py`) — `_resolve_model_keyword`
      used a raw `"pro" in text` substring test, so any short message containing "pro" as a
      substring of an unrelated word ("proje", "problem", "program", "profesyonel", "approve",
      "provide", ...) silently hijacked the turn into a model switch instead of being answered —
      the CLI `continue`s after a detected switch, so the user's actual message was dropped
      entirely, not just misrouted. Fixed with `\b`-anchored word-boundary regex matching per
      keyword; still correctly matches Turkish apostrophe-suffixed forms ("Pro'ya") since `\b`
      treats the apostrophe as a boundary.
- [x] **[BUG-emptyresp]** empty LLM response saved as success (`graph/nodes.py`) — `critic_node`'s
      fast-path fired on `not response_text OR revise_count >= 2`, so an empty final response was
      unconditionally accepted as "success" and the graph routed straight to END, even with
      revision budget remaining. Split into two cases: budget exhausted → accept, but substitute a
      visible fallback message instead of silence; budget remaining → `redirect` verdict, giving
      the agent an actual retry instead of ending the turn on nothing.
- [x] **[BUG-upload]** `/chat/upload` no size cap + no cleanup (`api.py`) — `await file.read()` was
      fully unbounded, and every non-image upload's raw copy under `data/uploads/` was never
      deleted, accumulating forever. Now reads in 1 MB chunks with a running total, aborting with
      413 as soon as `MAX_UPLOAD_BYTES` (50 MB) is crossed rather than buffering an oversized file
      first; the PDF branch deletes its raw copy immediately after extraction (nothing later needs
      it), the Excel/CSV/Word tool-hint branch deletes it in the SSE generator's `finally` (must
      wait — the agent may call `file_read`/`excel_read`/`csv_read` on it at any point while
      streaming).
- [x] **[BUG-usage]** `UsageTracker` clobbers across processes (`usage.py`) — `self._total` was
      loaded once at construction and mutated in place for the process's whole life; every save
      overwrote `data/usage.json` with a snapshot that got staler with every turn, so across two
      live processes (the CLI plus a long-running `--api` server) whichever saved last silently
      erased the other's recorded spend. `record()` now re-reads `_load_total()` fresh from disk
      immediately before merging its delta in and saving, under a new `threading.Lock` — narrows
      the failure window to a brief TOCTOU race rather than "guaranteed loss whenever two
      processes are alive together"; a real fix needs a cross-process file lock, which nothing
      else in this codebase uses either (see the docstring for the explicit tradeoff).
- [x] **Bonus fix, found live while fixing BUG-usage** (same root cause, safety-relevant, not in
      the original backlog): `kill_switch.py`'s `_load()` cached the first successful read for the
      rest of the process's life. `policy_guard.evaluate()` checks `kill_switch.is_enabled()` on
      every L3 call specifically so a trip takes effect immediately — but the load-once cache meant
      a trip from one process (e.g. the CLI's `/killswitch`) was invisible to any other
      already-running process (e.g. a long-lived `--api --monitor` server) until it restarted,
      silently defeating the "hard stop, no prompt" guarantee in exactly this project's targeted
      deployment shape. Now always re-reads from disk (the file is a few bytes; the only caller is
      already about to do far more expensive work) — `_cache` is kept only as a last-resort
      fallback for a transient read failure, not a steady-state optimization.
- [x] Electron/mobile client hygiene:
      **[BUG-elec]** neither of the Electron HUD's two REST calls (`App.jsx`'s file-drop-to-analyze
      → `/chat/upload`, `HudPanels.jsx`'s `BottomBar` chat input → `/chat/stream`) ever sent the
      `X-API-Key` header — both would 401 the moment `JARVIS_API_KEY` is actually configured.
      `BottomBar` didn't even accept an `apiKey` prop; now threaded through from `App.jsx`.
      **[BUG-mob-tls]** the mobile client's `/ws` token traveled as a `?token=` query param —
      visible to anything that logs URLs (proxies, access logs, OS/browser connection history).
      Switched to `IOWebSocketChannel` (Android-only via `dart:io`, fine — no Flutter Web target)
      so the token goes in an `X-API-Key` header instead; `jarvis/api.py`'s `ws_endpoint` now
      checks the header first, falling back to the query param only for Electron (whose browser
      `WebSocket` API genuinely cannot set custom headers on the upgrade request — not fixable
      client-side). This closes the URL-logging exposure, **not wire-level cleartext** — this
      server has no TLS termination, so confidentiality on an untrusted network still depends on
      tunneling through Tailscale, same as before; documented honestly in `docs/VOICE_PROTOCOL.md`
      rather than implied as fully closed. **[BUG-reconnect]** the WS reconnect loop retried every
      3s forever with no backoff — if the PC is off for hours, that's a reconnect attempt every 3s
      the whole time. Now exponential backoff (3s → doubling → capped at 60s), reset to 3s on a
      successful `channel.ready`.
      **[task_a9cee697, fixed 2026-07-15 same-day follow-up]** `WsClient.reconnect(host, apiKey)`
      accepted new host/key parameters but never applied them (`_host`/`_apiKey` were `final`) —
      now mutable and actually reassigned before reconnecting. Was dead code (no call sites) at the
      time; still no call sites, but now correct whenever mobile settings-switching wires one in.
- Note: offline resilience is largely already achieved by the local-first Faz 1-3 work; this
      phase's provider-router tests (above) are the first *persisted* proof of that claim rather
      than a one-off manual verification.

### Faz 8 follow-up — 2026-07-15 (same-day continuation session)

Picked up HANDOFF.md's own "recommended next steps" from the session above: the merge decision,
the Faz 0 bugs that were opportunistically deferred, and the `flutter analyze` verification that
needed a Flutter SDK not installed at the time.

- **Repo pushed to GitHub** (owner request): `langgraph-migration` → `main` fast-forward merge
  (see above), then a new public repo created by the owner and `main` pushed to it
  (`github.com/mertkaanakgunlu-debug/JARVIS`). Before pushing, the full git history was scanned for
  ever-committed secrets (`.env`, `credentials.json`, `token.json`, `.pem`/`.key` filenames, plus a
  content-pattern scan for Google/OpenAI/Slack API-key and PEM-private-key shapes across every
  commit's diff) — nothing found; `.env`/`data/` were already correctly gitignored, only the
  placeholder-only `.env.example` is tracked.
- **Flutter SDK installed** — no official winget package exists, so installed via
  `git clone https://github.com/flutter/flutter.git -b stable --depth 1 C:\flutter`, added to the
  user PATH. `flutter doctor`: Flutter SDK itself is fine; Android toolchain and Visual Studio are
  both absent (no Android Studio/SDK, no VS Desktop-C++ workload) — installing either is a
  multi-GB, separate undertaking, deliberately not done unprompted. `flutter pub get` +
  `flutter analyze` in `mobile/` both ran clean: **zero mentions of `ws_client.dart`** in the
  analyzer output (the Faz 8 BUG-mob-tls/BUG-reconnect/task_a9cee697 Dart changes compile and
  type-check for real, not just by inspection) and **zero `error`-severity findings anywhere** in
  the app — 69 pre-existing `info`-level deprecation notices (`withOpacity`→`withValues`,
  `partialResults`→`SpeechListenOptions`), unrelated to this session, left untouched.
  `flutter build apk` was not attempted (needs the Android SDK — see above).
- **BUG-15** (finance sync regex never matches, `tools/finance.py`) fixed — live-diagnosed root
  cause: `gmail.py`'s `_fmt_message()` actually emits `"• [id]  Subject"` (bullet-prefixed), but the
  `msg_ids` regex expected a bare `"[id]"` at line start, so it never matched and sync always
  reported zero messages regardless of what Gmail actually had. Same root cause had also broken the
  "read" step's subject/body extraction, which searched for a `"Konu:"`/`"---"` shape
  `_fmt_message()` has never produced (subject always came out empty; body included the header
  block). All three regexes fixed to match the real format.
- **BUG-16** (`todo('add')` background analysis silently fails, `graph/tools.py`) fixed —
  `asyncio.create_task(_bg_analyze())`'s result was never referenced anywhere; asyncio only holds a
  *weak* reference to a task, so an unreferenced one is eligible for garbage collection before it
  finishes, silently killing the prioritization before `store.update()` ever ran. Fixed with a
  module-level `_todo_bg_tasks` strong-reference set, pruned via a per-task done-callback.
- **BUG-17** (gcp_quota cache key never hits, `gcp_quota.py`) fixed — the freshness check tested
  `cached.get("rpm_pro") is not None`, but no code anywhere ever wrote a key literally named
  `"rpm_pro"` (only `"rpm_pro_used"`/`"rpm_flash_used"`/etc.), so it never matched and every call
  attempted a live Cloud Monitoring fetch regardless of cache state. `_load_cache()` already filters
  by TTL, so the fix is simply `if cached: return cached`.
- **BUG-18** (gcp_quota forecast daily-rate math wrong, `gcp_quota.py`) fixed — the forecast read
  `usage.json`'s `last_updated`, which `usage.py` refreshes on *every* save (effectively always
  "now"), so `days_elapsed` collapsed to 1 on every call and `daily_rate` became the entire all-time
  cost total instead of a real per-day average — a wildly overstated "spend forecast." Fixed by
  adding `first_seen` to `usage.py` (set once via `setdefault`, never overwritten after) and having
  the forecast anchor on that instead.
- **`_OllamaEF`/`_GeminiEF` missing `embed_query()`** (`jarvis/memory.py`, both previously flagged
  only for Ollama) fixed — live-reproduced root cause (not assumed): this project's installed
  chromadb version calls `embedding_function()` for `.add()` but
  `embedding_function.embed_query()` for `.query()` **unconditionally, no `hasattr` fallback**
  (confirmed via `chromadb/api/models/CollectionCommon.py`'s `_embed(is_query=True)`), so *every*
  semantic recall (`recall_facts`/`recall_procedures`/`recall`/doc RAG) against an Ollama- or
  Gemini-backed collection raised `AttributeError` the moment it queried — `.add()` alone always
  looked fine, which is why this had gone unnoticed. Both classes gained an `embed_query()` that
  delegates to the same logic as `__call__` (no query/document asymmetry needed for either backend
  as used here). Ollama path confirmed live against the real local Ollama server.
- **Bonus find while live-verifying `_GeminiEF` against the real Gemini API** (owner asked to
  "complete everything necessary"): `_build_gemini_ef`'s hardcoded model id
  (`"models/text-embedding-004"`) has been retired server-side — every real call 404'd. A real
  `client.models.list()` call found the actual current embedding models
  (`gemini-embedding-001`/`-2`/`-2-preview`); switched to `gemini-embedding-2`. Also added a
  construction-time smoke-test embed call (same reasoning as `_build_ollama_ef`'s reachability
  probe) so a future model-id retirement fails fast and falls through to the default ONNX EF
  instead of crashing every real recall call. Confirmed live: the error changed from `404
  NOT_FOUND` (wrong model) to `429 RESOURCE_EXHAUSTED` / "prepayment credits depleted" (this
  account's already-documented, pre-existing billing state — see MEMORY.md — not a code bug),
  proving the model id itself is now correct even though this account can't currently complete a
  real embed call to prove the full round-trip end to end.
- **12 new regression tests** (`test_finance_tool.py`, `test_gcp_quota.py`,
  `test_memory_embedding.py`, `test_todo_bg_analysis.py`) — full suite now **104/104 passing**.
- **Worktree cleanup, partial**: of the 21 stray `.claude/worktrees/*`/`claude/*` scratch branches,
  17 were confirmed via `git merge-base --is-ancestor` to be fully contained in
  `langgraph-migration` (zero unique content) and deleted. **4 kept** — they hold commits not on
  `langgraph-migration`: `claude/eager-noether-46af01` (5, subagent migration) and
  `claude/thirsty-mclean-f67665` (1, Calendar integration) look superseded by different
  implementations but weren't confirmed; `claude/gifted-wilbur-e021ea` (1, "eval regression suite —
  85 smoke tests, 25 golden scenarios") and `claude/stoic-spence-2c5246` (7, "rolling/hierarchical
  summarization") have no obvious equivalent in the current codebase and may be worth recovering —
  see [HANDOFF.md](HANDOFF.md). Don't delete these four without a separate explicit go-ahead.
- **Still deferred, unchanged**: Faz 6 (hardware-gated); Android SDK (not installed, see above);
  BUG-25's appendix-table checkmark (the fix itself shipped in Faz 2 — this is a stale table row,
  not an open bug, see the bug backlog appendix below).

---

## Bug backlog appendix (59-finding review, 2026-07-14)

Every finding from the review, mapped to the phase that owns it. The 15 highest-severity were
also reported via this session's code-review tooling.

| Tag | File:line | Summary | Phase |
|---|---|---|---|
| BUG-1 | graph/tools.py:132 | `python_run` unsandboxed, arbitrary abs paths, L2/no-confirm | 4 ✅ (reclassified; sandboxing itself deferred) |
| BUG-2 | api_routers/system.py:35 | `/system/wake` unauthenticated | 4 ✅ |
| BUG-3 | cli.py:606 | CLI text REPL never handles `ConfirmationRequired` → hangs | 4 ✅ |
| BUG-4 | agent.py:652 | confirm interrupt spoken as raw JSON in voice/HUD | 4 ✅ (voice; no HUD UI built — see Faz 4 deferred list) |
| BUG-5 | prompts/core/02_tool_policy.md:23 | "never ask before any tool" directive | 4 ✅ |
| BUG-6 | graph/nodes.py:344 | gating per-tool not per-action | 4 ✅ |
| BUG-6-ssrf | tools/webfetch.py:80 | no SSRF/private-range guard | 4 ✅ |
| BUG-8 | agent.py:481 | shared agent singleton mutated with no locking | 0 ✅ |
| BUG-9 | graph/graph.py:98 | checkpointer silently in-memory, ignores db_path | 0 ✅ |
| BUG-10 | session_store.py:186 | read/write lock asymmetry + non-transactional save_turn | 0 ✅ |
| BUG-11 | agent.py:333 | switch_session thread_id collision corrupts history | 0 ✅ |
| BUG-dup | session_store.py:216 | save_turn/load_history duplicate messages (cumulative snapshot read as delta) | 0 ✅ |
| BUG-backfill | agent.py:413 | summary backfill leaks unawaited coroutine; never actually runs at startup | 0 (leak) / GPT-5.6 remediation Faz 7 (feature gap) ✅ |
| BUG-12 | graph/streaming.py:26 | critic draft+revision concatenated, persisted | 3 ✅ |
| BUG-13 | agent.py:636 | chat_stream only catches GraphInterrupt, drops turn | 3 ✅ |
| BUG-14 | graph/nodes.py:144 | agent_node ainvoke unguarded | 4 ✅ |
| BUG-recursion | graph/state.py:11 | no recursion_limit / tool-call cap | 4 ✅ |
| BUG-confirm-payload | api.py:249 | `/chat` 500 loses ConfirmationRequired payload | 4 ✅ |
| BUG-15 | tools/finance.py:161 | finance sync regex never matches → feature dead | 8 ✅ (follow-up 2026-07-15) |
| BUG-16 | graph/tools.py:670 | todo('add') bg analysis silently fails | 8 ✅ (follow-up 2026-07-15) |
| BUG-17 | gcp_quota.py:106 | quota cache key never hits | 8 ✅ (follow-up 2026-07-15) |
| BUG-18 | gcp_quota.py:307 | forecast daily-rate math wrong → false alarms | 8 ✅ (follow-up 2026-07-15) |
| BUG-19 | monitor.py:390 | budget/quota alerts no dedup, re-fire every cycle | 7 ✅ |
| BUG-20 | tools/files.py:56 | file_write uncaught ValueError on home paths | 8 ✅ |
| BUG-21 | tools/calendar.py:261 | dedup guard hardcodes +03:00 | 8 ✅ |
| BUG-itu | tools/itu_mail.py:61 | IMAP connection leak on login failure | 8 ✅ |
| BUG-pdf | tools/pdf.py:54 | pdf cache keyed on path not content | 8 ✅ |
| BUG-geomath | tools/geo_math_tool.py:197 | Devito fallback hardcodes duration=0.5 | 8 ✅ |
| BUG-modelswitch | cli.py:116 | unanchored "pro" substring hijacks messages | 8 ✅ |
| BUG-22 | agent.py:514 | quota fallback ignores switch_model, stale label | 1 ✅ |
| BUG-emptyresp | graph/nodes.py:215 | empty LLM response saved as success | 8 ✅ |
| BUG-24 | config.py:125 | effective_cloud_model "pro" branch dead code | 1 ✅ |
| BUG-upload | api.py:391 | /chat/upload no size cap, no cleanup | 8 ✅ |
| BUG-23 | voice.py:138 | openwakeword buffer never reset between sessions | 3 ✅ |
| BUG-usage | usage.py:63 | UsageTracker clobbers across concurrent processes | 8 ✅ |
| BUG-25 | agent.py:552 | entity extraction fires every trivial turn | 2 ✅ |
| BUG-elec | electron/App.jsx | HUD never sends API-key header | 8 ✅ |
| BUG-mob-tls | mobile/ws_client.dart:22 | cleartext ws token in query string | 8 ✅ (query-param exposure closed; wire-level cleartext remains -- no TLS termination exists) |
| BUG-reconnect | ws_client.dart:42 | 3s reconnect forever, no backoff cap | 8 ✅ |
| task_a9cee697 | mobile/lib/core/ws_client.dart:84 | reconnect(host, apiKey) ignores its own params (_host/_apiKey final) | 8 ✅ (follow-up 2026-07-15) |

## Previously-completed refactor phases (context)

| Phase | What | Status |
|---|---|---|
| 1 | System prompt modularization | ✅ 2026-05-24 |
| 2 | ToolSpec risk metadata | ✅ 2026-05-24 |
| 3 | LangGraph confirmation-gate node | ✅ shipped, not functional end-to-end → Faz 4 |
| 4 | Memory-retrieval extraction into `ContextBuilder` | ✅ 2026-05-24 |

The old refactor "Phase 5-8" (TaskExecutor persistence, monitor-in-api, voice barge-in, sub-agent
migration) are now subsumed into the phases above (barge-in → Faz 3, monitor→chat → Faz 7, etc.).
