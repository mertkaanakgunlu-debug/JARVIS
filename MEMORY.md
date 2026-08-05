# MEMORY.md — Durable facts, decisions, and gotchas

> Facts that are true across sessions and rarely change. If something here turns out to be
> wrong, fix it in place rather than leaving a stale entry — this file is only useful if it's
> trustworthy. For "what's currently in flight," see [HANDOFF.md](HANDOFF.md) instead.

## Environment

- Machine: Windows, RTX 4070. Repo at `C:\Users\mertk\Desktop\Jarvis` (not OneDrive).
- Python 3.14.6, venv at `.venv/`. Activate: `.\.venv\Scripts\Activate.ps1`. (The original 3.13
  install this venv was built from — `AppData\Local\Programs\Python\Python313` — disappeared from
  disk during the project's ~7-week idle period; `.venv` was recreated 2026-07-14 against 3.14.6,
  the only version `py -0p` now finds. `requirements.txt` installs cleanly with `uv pip install`;
  plain `pip install` hits a `resolution-too-deep` backtracking failure on the Google Cloud SDK's
  loosely-pinned transitive deps — use `uv` instead, or pip will just spin.)
- Ollama required for `nomic-embed-text` local embeddings (`ollama serve`).
- MiKTeX required for LaTeX→PDF (`report_compile`). The code's auto-detect fallback path
  is currently hardcoded to the `mertk` Windows username (`jarvis/tools/latex.py`) — a bug
  if this ever runs under a different account, but the path itself (MiKTeX install location)
  is a real, durable environment fact.
- Vertex AI needs `gcloud auth application-default login` once for ADC; without it the app
  falls back to the AI Studio free tier automatically.
- `marker-pdf`'s first run downloads ~2-3 GB of layout models to `~/.cache/marker` — one-time cost.
- **Node.js LTS (v24.18.0) installed 2026-07-15**, user-scope via `winget install
  OpenJS.NodeJS.LTS --scope user` — the default machine-scope MSI install needs an interactive
  UAC elevation prompt that a non-interactive session can't click through (confirmed: it failed
  with exit 1602/user-cancelled); `--scope user` uses winget's zip-based extraction instead, no
  admin needed, installed to `%LOCALAPPDATA%\Microsoft\WinGet\Packages\
  OpenJS.NodeJS.LTS_Microsoft.Winget.Source_8wekyb3d8bbwe\node-v24.18.0-win-x64\`, persisted to
  the user (HKCU) PATH — a **new** terminal window picks it up automatically; a terminal/tool
  session already running when the install happened does not (Windows snapshots env vars at
  process start) and needs that directory prepended to `$env:PATH` manually for the rest of that
  session. `electron/`'s existing `node_modules` (already present on disk from before) installed
  cleanly with `npm install`, and `npm run build` (electron-vite) succeeded with zero errors
  across all 32 renderer modules + main + preload — this is a real build-tool verification of
  every `.jsx`/`.js` file touched by Faz 3, not just careful reading. **Still not done: actually
  launching `npm run dev`/the Electron GUI window** — a build passing doesn't prove runtime
  correctness (a logic bug that isn't a syntax/type error survives a build), and popping up a live
  desktop window wasn't in scope for what was asked. `.venv`'s Python was never affected either way.
- **Ollama installed 2026-07-14** (during Faz 1's rollout — `winget install Ollama.Ollama`), with
  `qwen2.5:7b-instruct` (4.7 GB) and `nomic-embed-text` (274 MB) pulled. The Windows installer's own
  auto-started service and a manually-launched `ollama.exe serve` raced for port 11434 once and left
  a hung listener (TCP connects, HTTP never responds) — if `python -m jarvis` seems to hang talking
  to Ollama, `Get-Process ollama*  | Stop-Process -Force` and relaunch clean
  (`Start-Process ollama.exe serve`, not both the tray app and a manual `serve`). Verified working
  end-to-end: plain `.invoke()`, tool-calling (`.bind_tools()`), and a full `JarvisAgent.chat()` turn
  through `build_graph()` all succeeded live in an isolated test dir.
- **Vertex ADC is missing** on this machine — live-tested via an actual
  `ChatGoogleGenerativeAI(vertexai=True).invoke()` call, raises `DefaultCredentialsError`; needs
  `gcloud auth application-default login` if Vertex Pro/Flash access is wanted. Not fixed (not asked).
- **The configured `GEMINI_API_KEY`'s AI Studio tier is intermittently rate-limited**, not
  permanently dead — re-tested multiple times same-session: sometimes a `gemini-2.5-flash` call
  succeeds outright (confirmed via a real `JarvisAgent.chat()` reasoning-role turn), sometimes the
  exact same call 429s with `RESOURCE_EXHAUSTED ... "Your prepayment credits are depleted"`. Treat
  that message as "back off and retry," not "this key is unusable" — don't over-conclude from a
  single failed call.
- **Ollama is not left running between sessions** — confirmed 2026-07-14 during Faz 2 verification
  (installed and working during Faz 1 earlier the same day, but `http://localhost:11434/api/tags`
  was unreachable by the time Faz 2 verification ran). Don't assume it's up; the default-ONNX-EF
  fallback path (see below) is a real, regularly-hit state on this machine, not a hypothetical.
- **ChromaDB's default ONNX EF distance scale is much wider than it looks at first glance** —
  measured live (2026-07-14, Faz 2 verification): a near-exact paraphrase scores ~0.07, a
  legitimately related but differently-worded query scores ~0.7, and something genuinely unrelated
  scores ~1.7+. A `distance_max` cutoff in the 0.45-0.6 range (which is what `recall()`'s existing
  `0.6` and `recall_summaries()`'s existing `0.55` use) will silently reject real, relevant matches
  under this EF specifically — it's fine for near-duplicate detection (tight thresholds, e.g.
  `find_similar_fact`'s `0.15`) but too tight for general semantic recall. `recall_facts`/
  `recall_procedures` (`jarvis/memory.py`) use `1.1`/`1.0` for this reason — see the comment above
  `find_similar_fact` in that file before adding another distance-thresholded recall method.
- **This project's installed chromadb version requires embedding functions to implement
  `embed_query()` separately from `__call__`** — confirmed live (2026-07-15):
  `chromadb/api/models/CollectionCommon.py`'s `_embed(is_query=...)` calls
  `embedding_function(input=...)` for `.add()` but `embedding_function.embed_query(input=...)` for
  `.query()`, **unconditionally, with no `hasattr` fallback** in the code path this project's
  `Collection.query()` actually hits. Any future custom `EmbeddingFunction` in `jarvis/memory.py`
  (`_OllamaEF`/`_GeminiEF` are the two so far) needs both methods or every semantic recall against
  it will raise `AttributeError` the moment it's queried — `.add()` alone will look completely fine,
  which is exactly why this went unnoticed for a while. The simplest correct implementation (used
  by both existing classes) is `embed_query = lambda self, input: self(input)` — neither backend
  used here needs a genuinely different query-vs-document embedding.
- **Gemini's embedding model id drifts — verify live, don't trust a hardcoded string.**
  `_build_gemini_ef`'s model id was `"models/text-embedding-004"` until 2026-07-15; live-tested
  that day and found retired (`404 NOT_FOUND ... not supported for embedContent`). A real
  `client.models.list()` call showed the actual currently-servable embedding models were
  `gemini-embedding-001`, `gemini-embedding-2-preview`, `gemini-embedding-2` — switched to the
  latter (current non-preview). `_build_gemini_ef` now also smoke-tests one real embed call at
  construction time (mirroring `_build_ollama_ef`'s reachability probe) so the *next* time Google
  retires a model id, this tier fails fast and falls through to the default ONNX EF instead of
  returning an object that 404s on every real recall call. Note: this account's `GEMINI_API_KEY`
  separately hits the already-documented "prepayment credits depleted" 429 (below) on embedding
  calls too, not just chat — same known account-level state, not a new/different issue.
- **GitHub remote configured 2026-07-15**: `origin` → `github.com/mertkaanakgunlu-debug/JARVIS`
  (public). **Updated 2026-07-21:** both `main` and `langgraph-migration` are pushed and track their
  origin counterparts. The earlier claim here that `langgraph-migration` was "local-only, identical
  to `main`" is **no longer true** — active work has been committed and pushed on
  `langgraph-migration` since, and `main` now trails it by 47 commits. `main` still has no commits
  of its own (a catch-up would be a pure fast-forward), but "all content is on `main`" is false;
  read `langgraph-migration` for current state. **4** `claude/*` scratch worktree branches remain
  local-only and unpushed (down from 21 — 17 were verified fully-merged and deleted 2026-07-15;
  see CLAUDE.md for the surviving four and why they weren't deleted).
- **`gh` CLI authenticated 2026-07-21** as `mertkaanakgunlu-debug`, token in the OS keyring, scopes
  `gist`/`read:org`/`repo`/`workflow`. Worth knowing because it is a *separate* credential store
  from git's: git pushes via `credential.helper=manager` (Windows Credential Manager) and works
  even when `gh` is logged out entirely. So "git push works" is NOT evidence that `gh` is
  authenticated — Claude Code's PR-status feature reads `gh`, and reported a misleading
  "authentication expired" when the real state was "never logged in" (no `hosts.yml` existed).
- **Flutter SDK installed 2026-07-15** at `C:\flutter` via `git clone
  https://github.com/flutter/flutter.git -b stable --depth 1` (no official winget package exists —
  `winget search flutter` returns unrelated apps tagged "flutter", not the SDK itself), added to the
  user PATH. `flutter doctor`: SDK itself fine; Android toolchain and Visual Studio both absent (no
  Android Studio/SDK, no VS Desktop-C++ workload) — either is a separate multi-GB install, not done
  as part of this. `flutter analyze`/`flutter pub get` work today; `flutter build apk` does not
  (needs the Android SDK).

## Direction: LOCAL-FIRST pivot (owner decision, 2026-07-14)

The project is pivoting from cloud-primary to **local-first**. Owner's reasons: Vertex credits
are likely expired (project sat ~7 weeks), and there's an **RTX 4070 (12 GB VRAM)** to run models
locally. Preference: run on own hardware for free unless cloud gives a dramatic quality gain.

- **Brain:** Ollama/Qwen becomes the PRIMARY tier; Gemini free tier (AI Studio, 1500 flash
  req/day) is optional escalation for hard reasoning only. This inverts today's architecture
  (Gemini primary → local fallback) into (local primary → cloud escalation). **Implemented
  Faz 1 (2026-07-14)** — see `jarvis/providers/get_llm(role, settings)`. `fast`/`local`/`realtime`
  roles resolve to Ollama first, cloud (`[Vertex Flash, AI Studio Flash]`, whichever configured)
  as an invocation-error fallback; `reasoning` resolves to cloud tiers first with local Ollama as
  its own final fallback (so nothing is cloud-mandatory, including escalation). A manual
  `switch_model()` pin (`Settings.pin_cloud_model`) bypasses the local-first default for the fast
  role only, same as pre-Faz-1 behavior for that command.
- **Voice:** goes fully local — streaming Whisper (faster-whisper large-v3-turbo, already
  installed) + Silero-VAD + Kokoro-82M/Piper TTS + barge-in. Gemini Live is an optional later
  cloud toggle, not the default. Not started (Faz 3).
- **Embeddings:** move to `nomic-embed-text` via Ollama (consistent, local, free). **Implemented
  Faz 1** — `jarvis/memory.py`'s `_build_ollama_ef()` is now the preferred embedding function for
  `jarvis_docs`/`jarvis_summaries` (probes Ollama reachability once at `Memory.__init__`, falls
  through to the existing Gemini embedder, then ChromaDB's default ONNX if neither is available).
  `jarvis_memory` (conversation recall) still uses ChromaDB's default ONNX EF unchanged — it
  already was local, and an EF can't be swapped on a collection that already has embedded data
  without breaking its vector space, so pre-existing installs keep whatever EF they were created
  with (the existing EF-conflict `except ValueError` fallback handles this automatically).
- **VRAM budget (4070, 12 GB):** can't run everything at once. Target concurrent config =
  Qwen2.5-7B-Instruct Q4 (~5 GB) + faster-whisper int8 (~2 GB) + Kokoro on CPU. 14B models are
  too tight alongside voice — keep 7B as the sweet spot, reserve 14B for brain-only scenarios.
- **Free win:** the research report's "offline resilience" goal comes largely for free under
  local-first — no separate mega-phase needed.
- **NOT doing:** true multi-vendor (OpenAI/Claude/Grok) — cost/complexity, low solo-user payoff.
  Build the pluggable provider *seam* (needed anyway), don't populate a fleet.

The full phased plan is [ROADMAP.md](ROADMAP.md) (Faz 0-8); the approved plan file is at
`C:\Users\mertk\.claude\plans\c-users-mertk-downloads-ki-isel-jarvis-gentle-phoenix.md`.
The north-star target came from an owner-commissioned research report
(`C:\Users\mertk\Downloads\Kişisel JARVIS Asistanı Geliştirme Planı.md`).

## Model / quota decisions

- Legacy (pre-pivot) primary path: Vertex AI (`gemini-2.5-pro` for critic/planner,
  `gemini-2.5-flash` for the ReAct executor), falling back to AI Studio free tier. Being
  reworked into the local-first router above (Faz 1).
- Gemini free-tier RPD: `gemini-2.5-pro` = 25/day, `gemini-2.5-flash` = 1500/day. Resets
  midnight UTC.
- Groq was evaluated and **rejected** as an orchestrator backend — the system prompt + tool
  schemas alone run ~5400 tokens, which blows through every Groq free-tier TPM cap that
  would still leave headroom for a real response. `GROQ_API_KEY` is commented out in `.env`
  by design, not an oversight. Only reconsider Groq for a deliberately small, single-step
  path (e.g. voice-only quick replies), not the main orchestrator.
- `.env.example`'s `GOOGLE_CLOUD_PROJECT` value looks like a real (not placeholder) GCP
  project ID — low-severity, but worth swapping for an obviously-fake placeholder if this
  repo is ever made public.

## Architecture decisions worth remembering

- LangGraph replaced the original pydantic-ai orchestrator (Faz 1-2, 2026-05-09). The old
  orchestrator lived at `jarvis/legacy/agent_pydantic.py` for reference only, unimported by any
  live path — **deleted outright in Faz 8 (2026-07-15)**, not archived; recoverable from git
  history before that commit if ever needed. `jarvis/prompts/system.md` (the dead pre-modularization
  system prompt pointer, superseded by `jarvis/prompts/core/*.md` since the original refactor) was
  deleted in the same pass.
- The 5 sub-agents (math, writer, research, coder, geomath) are still pydantic-ai, called
  directly with `await` from native `async def` `@tool` functions in `jarvis/graph/tools.py`
  (Faz 4, 2026-07-14 — previously bridged through a `_run_coro()` helper that spun up a second
  thread + fresh event loop even though these tools only ever ran inside an already-async graph;
  that helper is now dead code and was deleted from `graph/tools.py`. `jarvis/tools/finance.py`
  has its own separate, untouched `_run_coro`, unrelated to this). Migrating the sub-agents
  themselves to native LangGraph sub-graphs (not just how they're *called*) is still tracked as
  Phase 8 — not started.
- System prompt was modularized (2026-05-24, Phase 1) from one `system.md` into 6 concern
  files under `jarvis/prompts/core/` assembled by `jarvis/prompts/prompt_loader.py`. The old
  `jarvis/prompts/system.md` still exists as a dead pointer file (says so in its own header)
  — safe to delete once nobody's relying on it as documentation.
- `jarvis/prompts/core/05_memory_policy.md` and `06_context_injection.md` have their names
  and contents swapped relative to what you'd expect (05 = timezone/user-context rules,
  06 = the actual memory/entity/todo injection blocks). Known, not yet fixed — check both
  files if you're looking for either topic.
- ToolSpec risk metadata (`jarvis/tool_registry.py`, Phase 2) and the confirmation gate
  (`jarvis/graph/nodes.py`, Phase 3) shipped 2026-05-24 but didn't protect anything end-to-end
  until Faz 4 (2026-07-14) built `jarvis/policy_guard.py` and wired all three interfaces — see
  [CLAUDE.md](CLAUDE.md)'s safety section and [docs/SAFETY.md](docs/SAFETY.md) for the current
  (accurate, not aspirational) picture.
- `jarvis/providers/get_llm(role, settings, *, tools=, max_output_tokens=)` (Faz 1, 2026-07-14)
  is now the only place that constructs chat models — `graph.py`'s old `make_llm_fast`/
  `make_llm_pro` are gone. Two gotchas worth knowing before touching it: (1) `RunnableWithFallbacks`
  (what `.with_fallbacks()` returns) has no `bind_tools` — tools must be bound before wrapping, not
  after (`get_llm`'s `tools=` kwarg handles this internally; don't bind tools onto a bare role's
  result after calling `get_llm()` without `tools=` — request the tool-bound variant instead, as
  `graph.py` does for the `reasoning` role's two use sites). (2) Every cloud tier is constructed
  via `_safe_construct()`, which swallows construction-time failures (e.g. a missing API key) and
  drops that tier rather than raising — needed so `build_graph()` still succeeds on a pure-local
  config with zero cloud credentials, which is a legitimate, intended setup now.
- **5-layer cognitive memory (Faz 2, 2026-07-14):** Working (context window) and Episodic
  (`jarvis_memory`) were already solid; this phase built out Semantic, Procedural, and Meta.
  Design decisions worth knowing before touching any of it:
  - **Store pattern**: `jarvis/facts_store.py`/`jarvis/procedure_store.py` follow
    `todo_store.py`'s exact shape — their own `sqlite3` connection to the shared
    `data/sessions.db`, own `threading.Lock`, schema also registered in
    `session_store.py._apply_migrations` for the bootstrap-ordering safety net (same
    belt-and-suspenders pattern todos/finance/schedule already use). `Memory` (`memory.py`)
    never touches SQLite directly — it only owns ChromaDB + the vault; the two facts/procedure
    SQLite stores are constructed and owned by `JarvisAgent`, same as `todo_store`/`scheduler`.
  - **Episodic vs. semantic scoping is the crux of the whole design**: `Memory.recall()`
    (episodic, `jarvis_memory`) is session-scoped by default now (`session_id` filter) — raw
    past turns from other sessions must never leak in. `recall_facts()`/`recall_summaries()`
    are deliberately cross-session — that's the entire point of those two layers. Don't
    "fix" one to match the other; the asymmetry is intentional.
  - **`jarvis/prompts/CORE_VERSIONS.md` lives one directory above `jarvis/prompts/core/` on
    purpose** — `prompt_loader.py` globs every `*.md` file directly inside `core/` into the
    composed system prompt; a version-tracking file placed inside that directory would leak
    its own table text into every prompt sent to the LLM.
  - **Meta memory's "never agent-writable"** refers to the *runtime* JARVIS agent (via the
    `file_write` tool, now blocked for `jarvis/prompts/core/` by `PROTECTED_WRITE_PREFIXES` in
    `jarvis/tools/files.py`) — not Claude Code development sessions editing these files under
    direct human review, which is the human-edit path the design assumes.
  - **Procedural memory's "learning" is explicit, not automatic**: the `procedure_save` tool
    is agent-invoked when it judges a task worth remembering — there's no silent/automatic
    successful-sequence mining. Keep it that way unless explicitly asked to build the
    automatic version; it's a materially bigger, quality-riskier feature.
- **Real-time local voice + remote audio transport (Faz 3, 2026-07-14):** `jarvis/voice/` (package)
  replaced the flat `jarvis/voice.py`. Design decisions worth knowing before touching any of it:
  - **Silero VAD MUST stay pinned to v5.1.2, not a newer tag** — confirmed by live A/B testing
    against real (Piper-synthesized) speech, not assumed from docs: v6.2.1's exported `.onnx` graph
    declares the *exact same* input/output names and shapes as v5.1.2 (`input`/`state`/`sr` →
    `output`/`stateN`) but with the standard frame-by-frame streaming calling convention (raw
    ONNX, bypassing the `silero-vad` pip package — see below) it never produces a usable
    speech-probability signal: max ~0.33 across a 3-second spoken sentence, indistinguishable from
    noise. v5.1.2 scores real speech at 0.85-0.93 mean and silence/noise at 0.003-0.01. Re-verify
    with the same A/B method (`jarvis/voice/vad.py`'s module docstring has the exact commit SHAs)
    before ever bumping this pin — don't trust a matching I/O shape as "it'll behave the same."
  - **Don't install the `silero-vad` PyPI package** — its `pyproject.toml` hard-requires
    `torch`/`torchaudio` even though inference can run ONNX-only, which this project has
    deliberately avoided (see the VRAM budget note below). `jarvis/voice/vad.py`'s `SileroVAD`
    loads the raw `.onnx` weights directly via `onnxruntime` (already a dependency via
    openwakeword/faster-whisper/chromadb) instead.
  - **Piper voice names are verified against the live `rhasspy/piper-voices` `voices.json`, not
    assumed from general knowledge**: only `tr_TR-dfki-medium` (Turkish) and `en_US-lessac-medium`
    (English) are configured. Early research surfaced `fahrettin`/`fettah` as other Turkish Piper
    voice candidates — those are not currently published; don't reintroduce them without
    re-checking `voices.json` live first.
  - **Piper chosen over Kokoro-82M specifically because Kokoro doesn't support Turkish at all**
    (its language list is English/Spanish/French/Hindi/Italian/Japanese/Portuguese/Mandarin only)
    — this wasn't a close call given this user's primary language.
  - **`AudioIO` (`jarvis/voice/io_base.py`) is deliberately a *thin transport* Protocol** (raw
    frames in, PCM chunks out) — NOT a place for VAD/STT/TTS logic. All of that lives once in
    `RealtimeVoiceEngine` (`engine.py`), which is transport-blind and works identically whether the
    injected `AudioIO` is `DuplexAudioIO` (local sounddevice) or `RemoteWsAudioIO` (binary `/ws`
    frames). If you're tempted to add turn-segmentation or transcription logic inside an `AudioIO`
    implementation, that's the wrong layer — it would duplicate logic between the two backends.
  - **`VoiceModels`/`get_shared_voice_models()` (`engine.py`) is a lazy, process-wide singleton** —
    the local wakeword/PTT loop and every remote `/ws` session share ONE loaded Whisper/VAD/Piper
    instance rather than each reloading from scratch. Safe specifically because
    `jarvis/voice/session_manager.py` enforces at most one active engine instance at a time (local
    XOR one remote client) and `RealtimeVoiceEngine.events()` already resets VAD state at the start
    of every session. Don't add a second, competing model-loading path without that same
    non-concurrency guarantee.
  - **Barge-in has NO true acoustic echo cancellation** — a real DSP problem this project doesn't
    solve. Mitigation is a `SustainedGate` requiring *sustained, high-confidence* speech
    (`vad_barge_in_threshold`/`vad_barge_in_duration_s` in `config.py`, both higher/longer than
    normal turn-taking) during playback, specifically to reduce false triggers from the
    assistant's own voice bleeding from speakers back into an open mic. Headphones sidestep the
    problem; don't chase a full AEC implementation without being asked — this was a deliberate,
    documented scope boundary, not an oversight.
  - **Remote audio session arbitration is first-claim-wins, process-wide, not per-conversation** —
    `jarvis/voice/session_manager.py`'s `try_claim`/`release` are simple module-level state (no
    lock — safe because the check-then-set has no `await` in between on one event loop, same
    reasoning as the pre-existing unlocked `_ptt_event`/`_ww_stop` pattern in `voice_api.py`).
    Electron's main process always spawns the backend with `--wakeword`
    (`electron/src/main/index.js`), so starting a remote session must auto-pause the local loop's
    *next* claim (`pause_local_voice()`/`resume_local_voice()`) or the remote session would almost
    always get rejected as "busy."
  - **Full wire protocol is documented in `docs/VOICE_PROTOCOL.md`** — read that before extending
    the remote-audio path (e.g. the mobile fast-follow) rather than reverse-engineering it from
    Electron's JS (`electron/src/renderer/src/hooks/useRemoteAudioSession.js` is the only
    implemented client today).
  - **Electron JS changes in this phase are UNVERIFIED beyond careful manual reading** — this dev
    machine has no Node.js/npm on PATH (confirmed: not in Bash or PowerShell PATH, and the bundled
    `electron.exe`/`esbuild.cmd` under `electron/node_modules` couldn't be coaxed into a working
    standalone interpreter either), so none of the `.jsx`/`.js` files touched this phase have been
    syntax-checked, let alone run. Run `npm run dev` (or the project's normal Electron dev command)
    before trusting this code — see [ROADMAP.md](ROADMAP.md)'s Faz 3 verify section for the full
    human hand-off checklist.

- **Security kernel (Faz 4, 2026-07-14):** `jarvis/policy_guard.py` is the single place that
  decides "is this tool call allowed, does it need the user's OK" — design decisions worth
  knowing before touching any of it:
  - **Per-action risk lives in a small override table, not on the tool itself.**
    `policy_guard._READ_ACTIONS` maps `tool_name -> {read action names}` only for the four
    tools that actually mix risk levels (`google_calendar`/`gmail`/`google_drive`/`itu_mail`).
    Every other tool's actions all share its `ToolSpec.risk_level` uniformly. Don't add an entry
    for a tool that doesn't have this split — it's dead weight and implies a distinction that
    doesn't exist. Same word can mean different things per tool (`todo`'s `"done"` action marks
    a task complete — a write; `schedule`'s `"done"` action *lists* completed tasks — a read) —
    this is exactly why the table is keyed by tool name, not a single global read-verb set.
  - **Kill switch and confirmation gate solve different problems, don't conflate them.** The
    gate asks; the kill switch (`jarvis/kill_switch.py`) refuses to ask at all and hard-denies,
    for L3 (external-effect) actions only — it does NOT block L2 (local reversible writes like
    `file_write`/`todo`/`spotify`). This was a deliberate scope choice (an emergency stop for
    JARVIS acting on the *outside world*, not a full functionality halt), not an oversight — if
    asked to make the kill switch block more, that's a real behavior change, not a bug fix.
  - **Kill switch state is a file (`data/kill_switch.json`), not a `Settings` field, on
    purpose** — the whole point of an emergency stop is that it stays stopped across a process
    restart until someone deliberately re-arms it; a `Settings` field would reset to its
    `.env`/default value on every restart, defeating the mechanism.
  - **The audit log has two independent write sites recording different things** — don't assume
    one supersedes the other. `confirmation_node` (`graph/nodes.py`) writes `"decision"` entries
    (was this call allowed, did it need confirmation, what did the user say) at gate time.
    `agent.py`'s `_HudEventCallback` (a LangChain callback already attached to every graph
    invocation for the HUD feed) writes `"execution_start"`/`"execution_end"` at actual-run time,
    keyed by matching LangChain's `run_id` across the start/end callback pair — this is what
    makes it genuinely "did the side effect happen," not just "was it authorized."
  - **A background `TaskExecutor` job cannot answer a confirmation prompt** — there's no
    interactive channel. `ConfirmationRequired` (an `Exception` subclass) bubbling out of
    `agent.chat()` inside `TaskExecutor._run()` is caught specifically and turned into an
    actionable failure message, not silently retried or left to hang.
  - **Voice confirmation reuses the exact same `chat_stream()` interrupt path text mode's
    `ConfirmationRequired` exception represents** — `chat_stream()` (unlike `chat()`) never
    raises for a confirmation interrupt; it yields the `__jarvis_confirm__` JSON marker as one
    complete delta instead. Any new streaming call site must check for this marker
    (`jarvis/voice/session.py`'s `parse_confirm_marker`) before treating a delta as real text —
    forgetting this is exactly BUG-4 (JSON spoken/rendered verbatim).
  - **`python_run`'s L3 reclassification is an access-control fix, not a sandbox.** If asked to
    actually sandbox it (subprocess resource/network restriction), that's new work, not something
    already done — don't imply otherwise.
  - **The five sub-agent tool bridges are native `async def` now, not `_run_coro()`-wrapped** —
    `math_solve`/`write_content`/`research`/`generate_code`/`geo_math` (analyze branch) `await`
    their `run_*()` coroutine directly. Safe specifically because these tools are ONLY ever
    dispatched by LangGraph's `ToolNode` inside an already-running async graph
    (`agent.py`'s `self._graph.ainvoke()`/`.astream()`) — if a tool like this is ever needed from
    a genuinely sync call site outside the graph, it would need its own bridge again; don't
    assume every tool in `graph/tools.py` can be freely converted the same way without checking
    it's graph-only first.

- **MCP client layer (Faz 5, 2026-07-15):** `jarvis/mcp_integration.py`'s `McpToolManager` connects
  to configured external MCP servers and merges their tools into the graph as a second,
  dynamically-discovered tool source alongside the 36 native `@tool` wrappers — design decisions
  worth knowing before touching any of it:
  - **The persistent-session requirement was discovered empirically, not assumed from docs** — a
    LangChain forum thread independently confirmed the exact same failure mode a scratch test on
    this machine hit first: `MultiServerMCPClient`'s default, convenience `get_tools()` method
    creates a *fresh* MCP session (and therefore, for a stdio server, a fresh subprocess) **per
    tool call**. For a stateless server that's just wasteful; for a stateful one like browser
    automation it's silently broken — a `browser_navigate` then a separate `browser_click` call
    would land in two different, unrelated browser processes, the second with no page loaded.
    Fixed by always using `client.session(name)` (persistent, entered via an `AsyncExitStack` held
    for the manager's life) + `load_mcp_tools(session)`, never the default `get_tools()`. Verified
    live: `browser_navigate` then a *separate* `browser_snapshot` call correctly see the same page,
    through the actual compiled graph's `ToolNode`, not just a standalone script.
  - **That persistent session is event-loop-bound, and this project has several independent event
    loops in play** (the same root cause as **BUG-9**'s `AsyncSqliteSaver` rejection — see
    `jarvis/graph/graph.py`'s docstring). `JarvisAgent.__init__` runs before any loop exists in
    every real entry point (confirmed: `cli.py` constructs `JarvisAgent` before its own
    `asyncio.run()`; `api.py`'s `init_agent()` runs before uvicorn's loop starts) — so MCP tools
    cannot connect at construction time. They connect lazily via `connect_mcp_tools()`, which
    rebuilds `self._graph` with the newly-known tools (`build_graph()` gained an `extra_tools`
    param for this, and the switch_model()/quota-fallback rebuild call sites were updated to keep
    passing `self._mcp.tools` through so a mid-session model switch doesn't silently drop them).
    Critically, this method is called **explicitly, once, from each entry point's own real
    long-lived loop** (`cli.py`'s `_run_loop`/`_run_voice_loop`, right before their main
    while-loop; `api.py`'s `lifespan()`, before `yield`) — deliberately **not** lazily from
    whichever caller's `chat()` happens to fire first, because in API mode that could in principle
    be a `TaskExecutor` background job running on its own short-lived per-call `asyncio.run()`
    thread, which would bind the subprocess's stdio streams to a loop that's about to be destroyed.
    `chat()`/`chat_stream()` still call `connect_mcp_tools()` too, purely as an idempotent
    belt-and-suspenders safety net (matches this codebase's existing style, e.g.
    `session_store.py`'s migration-registration comment) — it only ever does real work once per
    process; every call after the first (including these) is a cheap no-op. Don't "simplify" this
    to a single lazy call inside `chat()`/`chat_stream()` without re-deriving whether that's still
    safe — it was deliberately NOT the primary path for the reason above.
  - **Fail-closed by design, not by omission**: `mcp_integration._classify()` only special-cases a
    short, explicit allow-list of Playwright tool names confirmed (live) to be pure inspection
    (`browser_snapshot`, `browser_take_screenshot`, `browser_console_messages`, `browser_find`, …)
    or inconsequential navigation (`browser_navigate`, `browser_wait_for`, `browser_tabs`, …) — L1
    and L2 respectively, no confirmation. **Every other tool name, including one this codebase has
    never seen (e.g. a future Playwright MCP version's new tool, or a completely different future
    MCP server's tools), defaults to L3 + `requires_confirmation=True`.** This mirrors
    `policy_guard._READ_ACTIONS`' existing per-action override pattern for the four mixed-risk
    Google/ITU tools (Faz 4) — same idea, keyed by MCP tool name instead of an `action` argument.
    If asked to add a new MCP server, resist the urge to pre-classify its whole tool surface as
    low-risk for convenience; extend the allow-list only for names you've actually confirmed are
    side-effect-free, same bar as the Playwright list was held to.
  - **Why a browser tool is a materially bigger step than `web_search`/`url_read`**: both already
    feed untrusted web text to the model (existing prompt-injection surface, unchanged by this
    phase), but neither gives the model *hands* — Playwright does. A poisoned page's text can get
    the model to *decide* to click/submit/type something, but the fail-closed default means it
    can't actually do so without the user approving that specific, described call
    (`policy_guard.describe_call()` — extended this phase with `element`/`url`/`text` in
    `_DETAIL_KEYS` so a pending `browser_click` confirmation actually names what gets clicked
    instead of showing the bare tool name). This is the project's own established mitigation
    pattern (see the Faz 4 security-kernel entry above), just applied to a new, riskier tool source.
  - **Windows: `npx` must go through `cmd /c`, confirmed live, not assumed** — `npx` on Windows is
    `npx.cmd`, a batch shim, not a real executable; Python's subprocess APIs (which don't invoke a
    shell by default) raise `WinError 2` spawning it directly. Every npx-based MCP server config
    (the shipped Playwright one, and the pattern documented in `.env.example` for
    `MCP_SERVERS`) uses `{"command": "cmd", "args": ["/c", "npx", ...]}`, never bare `"npx"`.
  - **Config is dedicated-flags-plus-escape-hatch, matching this codebase's existing per-integration
    convention** (Spotify/Calendar/Drive each get their own `Settings` fields rather than a generic
    blob): `mcp_playwright_enabled`/`mcp_playwright_headless` for the one server shipped this phase,
    plus a generic `mcp_servers: dict[str, dict]` (JSON-parsed from the `.env` string by
    pydantic-settings automatically) for anything else — e.g. Faz 6's ha-mcp should need a
    `MCP_SERVERS` entry, not new Python code.

- **Proactive self-initiation (Faz 7, 2026-07-15):** `JarvisAgent.proactive_turn()` gives
  `jarvis/monitor.py` a real path into the tool-calling graph — design decisions worth knowing
  before touching any of it:
  - **Isolated from the real conversation on purpose.** `proactive_turn()` runs its own message
    list (`[SystemMessage(_proactive_system_prompt(...)), HumanMessage(prompt)]`) and its own
    LangGraph thread_id, and deliberately never touches `self._history`/`_turn`/
    `session_store.save_turn` or episodic memory (`self.memory.store()`). Reusing `chat()` itself
    (or feeding `self._history` in) was considered and rejected — a background "should I say
    anything?" self-check becoming a visible, persisted turn in the user's actual session history
    would confuse every subsequent turn's context and get replayed as prior conversation on
    session resume. It still takes `_state_lock` (BUG-8) like every real entry point, so it can't
    interleave with a real turn's read-modify-write of shared agent state.
  - **Never raises `ConfirmationRequired` — by design, not an oversight.** There is no interactive
    channel for a background thread to answer one (same constraint `TaskExecutor._run()` already
    documented for user-initiated async tasks). A `GraphInterrupt` is caught, the pending
    confirmation is discarded (never stored in `self._pending_confirmations`, never resumable), and
    reported back as `ProactiveOutcome(kind="needs_confirmation", tools=[...])` so `monitor.py` can
    notify instead. Deliberately does NOT use the `POST /chat/confirm/{conf_id}` +
    `event_bus.confirmation_required` plumbing that already exists for this — per docs/SAFETY.md,
    no UI actually consumes that event yet, so a resumable-but-nothing-resumes-it confirmation would
    just leak forever; naming the gated action in a notification and pointing at the interactive
    path (which *does* work end-to-end) is more honest than pretending a dead-end round-trip is a
    real feature.
  - **A live verification run found a real gap the design didn't originally account for**: local
    `qwen2.5:7b-instruct`, given a mundane calendar-event trigger, hallucinated an unrelated
    `procedure_save` tool call. `procedure_save` is `risk_level=2` (`local_write`,
    `requires_confirmation=False`) — by policy_guard's own pre-existing, deliberate design ("kill
    switch is L3-only"), L2 writes execute without confirmation for a normal human-driven turn,
    where a person is present to notice. A background self-check has nobody watching, so this is a
    real gap Faz 7 newly *exposes* (the L2-no-gate design isn't new; being reachable with nobody
    watching is). Mitigated by tightening `_proactive_system_prompt()` to explicitly forbid any
    creating/saving/sending/modifying tool call during a proactive check (investigation must stay
    read-only; a suggested action goes in the reply text, not a live tool call) — this is a
    prompt-level mitigation on a non-deterministic model, NOT a structural guarantee the way the L3
    gate is one. Don't describe this as "fixed" if asked — it's narrowed, not closed. A real
    structural fix (a separate, read-only-only tool set for proactive turns, built via its own
    `build_graph()`/`get_llm(..., tools=...)` call) would close it properly; not built this phase —
    real added complexity (a second compiled graph to keep in sync with the main one on every
    model-switch/MCP-connect) for a feature that ships fully off by default
    (`monitor_proactive_enabled=False`).
  - **Rate-limited across ALL proactive sources combined, not per-source, and deliberately coarse.**
    `monitor_proactive_min_gap_sec` (default 600s) throttles actual `proactive_turn()` calls; a
    throttled item still gets its normal toast, just skips the extra LLM judgement call. A burst of
    e.g. a dozen unread emails after being offline will only get ONE of them looked at per gap
    window — accepted trade-off (ROADMAP.md's own words: "prevent runaway loops" is the goal, not
    "guarantee every item gets reviewed").
  - **`--monitor` now actually starts under `--api`** (`api.py`'s `lifespan()`, gated by
    `run_server(..., monitor=True)`) — previously silently ignored there (`docs/ARCHITECTURE.md`'s
    Known-gaps table had this tracked as unstarted since the original refactor backlog). This
    matters more than the CLI case: the API server is the long-running process a phone/HUD actually
    talks to, so it's where proactive monitoring needs to run to be useful in practice.

- **Cleanup & consolidation (Faz 8, 2026-07-15):** retired `jarvis/legacy/` (see the entry near the
  top of this section), added the first real test suite (`tests/`), and fixed 9 P2 bugs + 3
  client-hygiene items from the 59-finding backlog. Design decisions worth knowing before touching
  any of it:
  - **Cross-process shared-file staleness is a recurring bug shape in this codebase — check for it
    whenever you touch a module with a `_cache`/loaded-once pattern over a `Path("data")/...` file.**
    BUG-usage (`usage.py`) and a bonus find in `kill_switch.py` were the same root cause: a value
    loaded once (at construction, or into a module-level cache on first read) and never refreshed,
    so a second live process's write to the same file was either silently lost (usage.py's
    `_total` got overwritten by whichever process saved last) or silently ignored (kill_switch's
    `_cache` never noticed another process's trip). The fix pattern both share: re-read fresh from
    disk immediately before the next read-modify-write, rather than trusting an in-memory snapshot.
    This narrows the failure window to a brief TOCTOU race, it doesn't eliminate it — a real fix
    needs a cross-process file lock, which nothing in this codebase uses (not even `audit_log.py`,
    which sidesteps the whole class of bug by being append-only instead of read-modify-write).
    Don't reach for a new locking library to close that residual gap without discussing it first —
    it wasn't judged worth the dependency for a single-user local assistant where the race window is
    milliseconds, but that's a judgment call, not a settled fact.
  - **`kill_switch.py`'s `_load()` intentionally does NOT cache across calls anymore** (it did
    before this phase) — see the file:line entry in ROADMAP.md's Faz 8 section for the full
    incident. If you're tempted to add caching back for performance, don't: the only caller
    (`policy_guard.evaluate()`) is already about to do far more expensive work (an LLM-gated tool
    call), and the whole point of checking live is that a kill-switch trip must be visible on the
    *next* call from a different, already-running process — that's the property caching broke.
  - **New `tests/` suite is deliberately "minimal," not comprehensive** — 92 tests *at the time*
    (559 today — this bullet describes what Faz 8 built, not current scope), covering
    `policy_guard`, `session_store` concurrency, the provider router (this is where the
    "offline-failover" claim now has a persisted test, not just a manual verification write-up),
    and one regression test per bug fixed this phase. Most tool modules (calendar/gmail/drive
    actions beyond the dedup-guard fix, spotify, finance, todo, scheduler, ...) still have zero
    coverage — extend `tests/` incrementally as you touch those areas, rather than writing a
    throwaway verification script the way every prior phase did (see this file's own
    isolate-test-data-paths lesson, which motivated `tests/conftest.py`'s `isolated_cwd` fixture —
    read its docstring before writing a test that constructs `SessionStore`/`UsageTracker`/
    `kill_switch`/anything else resolving `Path("data")/...` relative to cwd).
  - **BUG-mob-tls's fix is a real, but partial, mitigation — don't describe it as "encrypted."**
    Moving the mobile client's auth token from a `?token=` query string to an `X-API-Key` header
    (`IOWebSocketChannel` supports custom headers; Electron's browser `WebSocket` API cannot, so it
    still uses the query param) stops the token from being written into anything that logs URLs
    (proxies, access logs, OS/browser history). It does NOT add transport encryption — this server
    has no TLS termination at all, so a packet sniffer on the same unencrypted network segment still
    sees the header in plaintext exactly like it would the query param. Confidentiality on an
    untrusted network still depends entirely on the phone reaching the host via Tailscale (already
    the documented deployment path in CLAUDE.md), not on this fix. Standing up real TLS termination
    would close this properly; not built this phase — genuinely new infrastructure (a cert, uvicorn
    `ssl_certfile`/`ssl_keyfile` config), out of proportion for a client-hygiene bug fix.

## GPT-5.6 review remediation (owner decision, 2026-07-15)

An external GPT-5.6 static review of this repo, verified claim-by-claim against real code in one
session and implemented (7 phases, P0 security first) in the next — see CHANGELOG.md's
"[GPT-5.6 review remediation]" entry for the full per-phase writeup, HANDOFF.md for that session's
state. Durable facts worth knowing beyond that session:

- **`jarvis/api.py`'s bind host is no longer always `0.0.0.0`.** `resolve_api_bind_host(settings)`
  now governs it: empty `JARVIS_API_KEY` defaults to `127.0.0.1` (was `0.0.0.0` unconditionally) and
  *fails startup* if `JARVIS_API_HOST` is explicitly set to something non-loopback with no key. If a
  fresh `python -m jarvis --api` run ever refuses to start with a host-related `RuntimeError`, this
  is why — set `JARVIS_API_KEY` in `.env`, don't work around the check.
- **CORS is an explicit allowlist now** (`resolve_cors_origins()`), never `"*"`. Always permits the
  Electron desktop client (`file://`) and any `localhost`/`127.0.0.1` origin; anything else needs
  `JARVIS_API_CORS_ORIGINS` (JSON array) in `.env`.
- **Proactive turns (`monitor.py` → `JarvisAgent.proactive_turn()`) structurally cannot execute an
  L2 (auto-approve) or non-gated L3 tool call anymore** — `jarvis/graph/nodes.py`'s
  `confirmation_node` blocks any risk_level≥2 call on a `transport="monitor-*"` turn unless it's
  already going through the pre-existing L3-interrupt-to-notification path. This was previously only
  a system-prompt instruction ("don't do this during a background check"), i.e. not actually
  enforced — now it is, in code.
- **Agent-written procedures (`procedure_save`) start as `draft`, not immediately recallable.**
  `jarvis.memory.Memory.recall_procedures()` filters to `status='approved'` only — a human must run
  `/procedures approve <id>` (CLI) first. Seed procedures (`source='seed'`, the static workflow
  files migrated at startup) are still immediately `approved`.
- **`TaskExecutor`'s background jobs (deep research, reports, sims) go through
  `JarvisAgent.background_turn()`, not `.chat()`.** If you're debugging why a background task's
  exchange doesn't show up in the live conversation *while it's running* — that's intentional; it
  gets appended to `self._history` only after it completes. `chat()`/`chat_stream()`'s own BUG-8
  locking (the whole-turn `_state_lock` hold) was deliberately left untouched by this pass.
- **`jarvis/url_policy.py`** is the shared SSRF guard (localhost/private/link-local/metadata,
  resolved-IP checked for DNS-rebinding) — used by both `url_read` and MCP's `browser_navigate`
  interceptor. Add any *new* URL-fetching tool through this, not a fresh ad-hoc check.
- **`shell.py`/`python_exec.py`'s deny-lists are a basic guard, not a sandbox.** Still true after
  this pass (`docs/SAFETY.md`'s "Known limits" stands) — `python_run`'s subprocess still has no
  resource/network isolation, just a source-text substring scan before it's allowed to start.
- **`ruff` is now part of this project** (`.github/workflows/ci.yml`, `[tool.ruff]` in
  `pyproject.toml`) — not a `requirements.txt` runtime dependency (dev-only), install separately
  (`pip install ruff`) to run `ruff check jarvis/ tests/` locally. `E402`/`F841` are deliberately
  ignored project-wide (see the pyproject.toml comment for why) — don't "fix" those if you see them.
- **`requirements-lock.txt`** is a `pip freeze` snapshot of the proven-working `.venv/`, not a `uv
  lock` resolution — regenerate it after intentionally changing `requirements.txt` and confirming
  `pytest` is still green (see the file's own header).

## Agent Runtime rev.2 (owner decision, 2026-07-20)

A dev-focused GPT session reviewed the prior session's findings (context leakage, wrong tool
args, fabricated success claims, run-to-run variance) and proposed fixing the common
architectural root cause instead of patching each symptom — not new domain tools, a runtime
contract that makes JARVIS reliably use *any* tool. A reviewer gave 14 revisions to the first
draft (TaskContract missing, confirmation approving raw unvalidated args instead of a bound
ExecutionRequest, idempotency wrongly deferred past the phase that claims "duplicate side
effect: 0", `python_run` left as an accepted open hole while claiming alpha-readiness, no
Workflow Runtime phase despite the alpha gate requiring long-workflow evidence). All 14 were
applied. The approved plan (9 phases, 0-8) lives at
`C:\Users\mertk\.claude\plans\c-users-mertk-desktop-gpt-analysis-md-s-delegated-scone.md`.

- **This initiative's Faz 0-8 is NOT [ROADMAP.md](ROADMAP.md)'s Faz 0-8** (the local-first
  pivot, all done/deferred by hardware). Two unrelated plans happen to both use "Faz 0-8" —
  code comments and docs always qualify it as "Agent Runtime rev.2, Faz N" for exactly this
  reason. Don't assume a bare "Faz 0" reference in new code is either one without checking
  which initiative the surrounding comment names.
- **Faz 0 shipped 2026-07-20**: `jarvis/tool_registry.py`'s `ALPHA_STATUS_VALUES` (closed
  5-value vocabulary) + `_ALPHA_STATUS` + `get_alpha_status()`. `python_run` is `"disabled"` —
  removed from `make_tools()`'s returned list (the single chokepoint agent_node/ToolNode/the
  router all read from) AND independently vetoed in `policy_guard.evaluate()` (defense in
  depth against a stale pre-change checkpoint replaying the call). `shell_run` is
  `"quarantined"` (still exposed). Every other tool defaults to `"shadow_validated"` —
  documented as a Faz-1 destination, not a live signal; nothing measures shadow validation
  yet. `PolicyDecision.veto_kind` distinguishes this veto from the kill-switch one so
  confirmation_node's ack message names the real reason.
- **Faz 1 shipped 2026-07-21** (commit `1a22d7f`): new `jarvis/execution/` package — `contract.py`
  (`TaskContract`/`ExpectedOutcome`, shape only, no extractors yet), `postcondition.py`,
  `envelope.py` (`ExecutionEnvelope` + `build_shadow_envelope()`), `redaction.py` (the shared layer,
  see the redaction bullet below). `ToolSpec` gained 6 additive fields (`args_schema`,
  `postconditions`, `idempotency`, `effect_scope`, `contract_status`, `timeout_class`) — **none
  change live behavior yet**; they're Faz 3/6/7 destinations. `Settings.execution_contract_mode`
  (`off|shadow|enforce_read_only|enforce_reversible|enforce_all`, default **off**) gates a shadow
  ledger in `tool_result_accounting` that builds one envelope per tool call as a **pure observer**.
- **How Faz 1's "shadow changes nothing" claim was actually proven — the method matters more than
  the result.** A live 5×13 A/B **cannot** answer it: at n=5 per-scenario variance exceeds the
  effect (champ 62/65, champ-shadow 59/65, ctl-off 59/65 — the two Faz-1 arms tied while losing
  *different* scenarios). It was proven instead by a **deterministic replay**: the real compiled
  graph driven by a **scripted model** (model variance zero by construction), the same 9 fixtures
  run through `off` and `shadow`, requiring every externally observable outcome to match — tool
  selection, raw args, results, user-visible response, filesystem side effects by content hash,
  error text, counters, ledger. Only `execution_envelopes` may differ; volatile fields are stripped
  by explicit allowlist, not a blanket ignore. **The test's discriminating power was verified by
  mutation, not assumed** — injecting a shadow-only side effect turned 7 of the 9 red (the 2 that
  stayed green are the ones where no tool runs, exactly where the mutation can't fire). It runs
  against a real `SqliteSaver`, because shadow carries extra state *through the checkpointer* —
  `checkpointer=None` would have skipped the very mechanism under test. Reach for this shape
  (scripted model + mutation check) whenever a live score is too noisy to answer an
  equivalence question.
- **The model-selection decision (`qwen3:8b` + `LOCAL_REASONING_EFFORT=none`) is CONFIRMED**
  (was provisional per `a6a3426`; the owed re-baseline ran 2026-07-20). Under the two-metric
  oracle it scored **62/65**, beating the pre-two-metric 60/65: all four safety scenarios 5/5 and
  the historically-flaky G17b clean. The two remaining findings (B6 3/5, F16 4/5) are live
  confirmation of the hallucinated-tool-success pattern this whole initiative exists to fix
  architecturally — not a reason to revisit the model. **Caveat: that 62/65 reference is slightly
  contaminated** — a stray smoke run's requests landed inside its run 5 (see the harness entry
  below). Re-run it if Faz 2+ needs a pristine baseline.
- **The redaction gaps found while reading the audit path are CLOSED** (Faz 1, commit `1a22d7f`).
  For history: `tool_trace.record()` already redacted sensitive arg keys, but `audit_log.record()`'s
  `args_preview`/`result_preview`, `tool_trace`'s own `content_head`, and
  `tool_execution_ledger.content_head` (which reaches the LangGraph checkpointer's SQLite file) did
  not — a `gmail send` body was masked in the trace and readable in the audit log. All four now go
  through `jarvis/execution/redaction.py`, the shared layer, which also adds **pattern-based**
  redaction closing the old `redact_tool_args` hole where plain-string args were never scanned.
- **A measurement harness can silently score against the WRONG JARVIS — assume nothing from a
  clean exit code.** `ab_run_config.ps1 -Port` never reached `manual_test_driver.py` (it resolves
  its target from `JARVIS_TEST_BASE_URL`, which the wrapper never set), so every non-default-port
  run hit the default `8132` instead. Two distinct failures came from this: (a) if nothing was
  listening, every scenario got ConnectionRefused and the run still wrote a full-size results file
  and **exited 0**; (b) if a *previous* run's server was still up on 8132, the driver scored 13
  scenarios against **that** server — a plausible, entirely meaningless result, which is what
  produced the "isolated runs answer as Gemini 2.5 Pro" anomaly. Fixed 2026-07-21 plus guards
  against the class: preflight before scoring, a transport-vs-semantic failure taxonomy (an
  unreachable turn is *absent*, not *failed* — averaging it in understates the model), exit-code
  propagation through the PS wrapper (it used to log the driver's exit code and return 0 anyway),
  a per-run manifest, and `GET /internal/test-identity` (test-mode-only) proving the driver reached
  **the** server with **the** config, not just *a* server.
- **Faz 6 (Parts 1-2) shipped 2026-07-22**: `jarvis/execution/args_schemas.py`'s 12 pydantic
  schemas (`extra="forbid"`, per-action required-field validators verified against each tool's
  real dispatch body) are wired into `prepare_execution_node` as a **reject-only gate** — raw args
  always execute unchanged; a schema failure never substitutes a "corrected" form back into the
  call (closing a digest/execution divergence risk a review caught). `confirmation_node` gained an
  explicit bounded-repair state machine: one whole-batch reject-and-retry per turn, then a
  composed honest error — deliberately NOT implicit in `max_tool_rounds_per_turn`. A later session
  investigated the plan's remaining "alternative capability" repair rung against the real 12
  schema'd tools and **deliberately did not implement it** — no safe, mechanical substitute exists
  in this tool set (every candidate either reaches a different destination, like `gmail`↔
  `itu_mail`, or requires a content judgment call, like `schedule`→`todo`) — see CHANGELOG.md.
  That same session built the API's real per-client `conversation_id` support (`jarvis/api.py` +
  `JarvisAgent._switch_session_locked()` + `SessionStore.ensure_session()`) — see
  [[project-agent-runtime-rev2]] for full detail. Committed 2026-07-22 as `d6ce968`
  (conversation_id feature) + `a2a1bb3` (unrelated auth_setup.py fix); not yet pushed as of that
  commit — verify against `git log` before trusting the push status specifically.
- **Faz 7 (both Part 1 and Part 2) committed+pushed, CI green (`c1d12bb`) — the whole 9-phase Agent
  Runtime rev.2 plan now has only Faz 8 left**: new `jarvis/execution/workflow.py`/`workflow_store.py`/
  `workflow_engine.py` — a standalone `WorkflowEngine` (dependency-ordered steps, a step budget,
  SQLite checkpoint/resume, approval pause mirroring `confirmation_node`'s HMAC binding without a
  LangGraph interrupt, and narrow auto-compensation for exactly two registered true inverses:
  `file_write` and `todo`'s `"add"`) that reuses Faz 1-4's execution contract per step rather than a
  second verification vocabulary. Part 2 gives it a real entry point: `workflow_start`/
  `workflow_status` tools (validated against the same alpha-filtered tool list the model sees, so a
  step can never reach `python_run`), plus a human-only `/workflow approve|deny` CLI command —
  deliberately NOT a third tool, since exposing approval to the agent would let it resolve its own
  confirmation gate. See [[project-agent-runtime-rev2]] and CHANGELOG.md for full detail, including
  two real bugs a test caught before shipping (a step-budget-counter miscount in Part 1; a
  "workflow" router-pattern collision with an existing procedure-save test in Part 2). Verify
  against `git log` before trusting Part 2's commit status.

## MVP: mail → cash-flow → Excel → chart (owner decision, 2026-07-30)

The owner set ONE acceptance target — *"Maillerimi kontrol et, hesabımdaki para akışını analiz et,
bir excel tablosuna dönüştür ve grafikle"* — to settle whether JARVIS can chain dependent tools on a
real task. Owner constraints: text-first (voice parked), **strictly local** (`CLOUD_POLICY=off`
stays), Burgan mails as the data source, and a multi-sheet analysis workbook as the output.

Durable facts from building it:

- **A domain at the router's tool cap starves every other domain.** `data` held exactly 8 tools and
  `MAX_TOOLS_PER_TURN` is 8, so once `data` was primary no second domain could be added — the MVP
  prompt routed to `[data, mail]` and exposed 8 data tools with `gmail` AND `finance` invisible. The
  model then reported on a mailbox it had no tool to open. Fixed by splitting `data` into
  `data`/`report`/`math` **and** reserving a slot per additional routed domain in
  `select_tool_names` (the class fix, not just the instance). `tests/test_domain_closure.py` now
  asserts no domain reaches the cap — keep that green rather than raising the cap.
- **`\bhesapla` (calculate) vs `\bhesab` (my account) is a real Turkish routing trap.** Use the
  `\bhesab` stem for finance and never `\bhesap` — the latter is also the stem of *hesapla*, so it
  drags every arithmetic request into finance.
- **The API's async heuristic matches bare substrings against short Turkish words.** `"grafik"` is
  in `ASYNC_KEYWORDS`, so the owner's own MVP sentence was silently handed to the background
  `TaskExecutor` and `/chat` answered `{"async": true, task_id}` in ~0s. `cli.py` never consults
  that heuristic, so the **same sentence is interactive in the terminal and asynchronous over
  HTTP** — the phone/HUD is the affected surface. `ChatRequest.force_sync` now makes the
  interactive path explicitly reachable (`_should_offload()` holds the precedence). Narrowing the
  keyword list is an unmade product decision about phone UX.
- **Deterministic-first extraction beats LLM-first for template mail, and it is not close.**
  `jarvis/finance_parser.py` resolves the whole fixture corpus in ~0.00s per mail with zero
  inference; the previous cloud-only extractor returned None for every mail under
  `CLOUD_POLICY=off`, so `finance('sync')` was a **permanent silent no-op** (transactions table sat
  at 0 rows). Turkish grouping is the subtle part: `1.850,00` is 1850.00, and en-US parsing loses
  three orders of magnitude silently.
- **Never escalate an unrecoverable rejection to a model.** `no_amount`/`no_date` cannot be rescued,
  because the extractor validates every model-supplied value against text actually present in the
  mail — so the only possible outcomes are "guessed, caught" or "declined". Escalating anyway cost
  5.2s+1.8s of local inference and, under real turn contention, pushed `finance('sync')` past its
  60s ToolSpec timeout. **The worker thread still committed 7 transactions while the awaiting side
  was cancelled**, so the call left no `execution_end` and no `tool_trace` row and the model never
  learned it had worked. Diagnosed via the audit log's 3 `execution_start` / 2 `execution_end`
  asymmetry — a useful signal whenever a side effect exists with no trace row.
- **qwen3:8b lands 2 dependent tool calls, not 3.** `sync → export` is reliable (**10/10** once the
  descriptions were right); the third hop failed every way available — invented English column names,
  the ledger sheet instead of the chart sheet, the literal placeholder `path='path_to_file'`, and once
  the whole call emitted as a JSON block in the reply instead of being invoked. It repairs one
  argument per round but cannot hold the set together. So `finance('export')` produces the chart
  itself. **Carry the load in the tools, not the model**: the model chooses the period, never the data.
- Three things that measurably moved the model, worth reusing: put the next instruction (and the
  figures it must relay) in the **first two lines** of a tool result — a hint on line 5 was read
  straight past; order a workbook so the **chartable sheet is first**, because `plot_data` without
  `sheet=` reads the first one; and make a "column not found" error **name the other sheets**, which
  turns a dead end into a self-correcting one.
- **Negative/conditional language in a tool DESCRIPTION suppresses tool-calling outright.** The
  single most expensive finding of the session. Adding this to `finance`'s docstring —
  *"REQUIRES data in the store: call sync FIRST … export on an empty store fails"* — took the gate
  from `ALL STEPS 4/5` to **0/10 with ZERO tool calls in every run**: the model produced prose
  narrating what it would do instead of calling anything. Rephrasing the same fact positively
  (*"If the request mentions mail/e-posta, call sync first in the same turn, then export"*) restored
  **10/10**. Same model, same router, same tools; only the wording differed. Telling a small model
  what a tool needs is fine; telling it how the tool *fails* makes it avoid the tool.
  Corollary: a description edit is a behavior change and needs a re-measurement, exactly like a code
  change. The owner caught the bad state by questioning a suspicious number in the output.
- **Diagnostic ladder that localised the above quickly**, worth reusing when tool-calling dies:
  bind the tools to the model directly with a one-line system prompt (isolates model capability),
  then the full system prompt (isolates the prompt), then `fast` vs `reasoning` role (isolates the
  provider path), then the graph. Empty `tool_trace.jsonl` **and** empty `audit_log.jsonl` together
  mean the model emitted no tool call at all — the graph never got one to gate.
- **`_route_query()` sends every non-conversation query to the `reasoning` role**, which under
  `CLOUD_POLICY=off` is local Ollama with **thinking ON and max_output_tokens=2048** (the `fast` role
  passes `reasoning_effort="none"` and 4096). Measured: `fast` answers the MVP prompt in ~0.9s with
  19 output tokens; `reasoning` takes 12–33s and 524–1360 tokens for the same call. Both do emit
  correct tool calls, so this was not the tool-calling failure — but on a local-only config every
  tool-shaped turn pays that cost, which is worth knowing before optimising latency.
- **qwen3:8b tool output is non-deterministic at temperature 0.** The same prompt+tools returned
  `[sync, export]` and then `[export]` minutes apart. Never conclude anything from n=1; the swing
  between two 5-run batches with no production change in between was 4/5 → 0/5.
- **Excel formula injection was unguarded anywhere in this repo.** Merchant/description text comes
  from email a stranger can send, and Excel executes a leading `=`/`+`/`-`/`@` on open —
  `jarvis/tools/workbook.py:sanitize_cell()` is the only guard; reuse it for any future
  spreadsheet writer.
- `finance` is `side_effect_type="local_write"` (was `external_read`) now that `export` writes a
  file, and its export path is **deterministic + overwritten** (`exports/cashflow_<YYYY-MM>.xlsx`)
  to honor its registered `idempotency="natural"`. Do NOT use `RunContext.for_execution()` for it —
  that mints a fresh dir per call. And it cannot live under `data/`, which `files._resolve()` refuses
  via `PROTECTED_DIRS`.
- **XLSX idempotency must be asserted semantically, never byte-for-byte** — it is a zip with
  embedded timestamps.
- **Gmail and Drive tokens were revoked while Calendar's survived** (2026-07-30, `invalid_grant`):
  Gmail/Drive use Google *restricted* scopes, Calendar only *sensitive*. `google-auth-oauthlib` was
  also missing from the venv **and undeclared in `requirements.txt`**, and `InstalledAppFlow` was
  imported unconditionally — so a valid token could not be used without the interactive-consent
  package. Re-auth is `python scripts/auth_setup.py gmail drive`; that script had **no Gmail section
  at all** and its `if token.exists(): skip` reported a revoked token as healthy.

## PDF statement import — the path that actually carries real data (2026-07-30)

The owner's bank is **Burgan**, consumer brand **ON**, and it exports account history
as **PDF only** (no CSV/Excel). Their mailbox contains **no transaction notification mails at all**
— verified live across every folder including spam. So `finance('import_statement', path=...)`
(`jarvis/finance_statement.py`) is how real money data reaches JARVIS today; the mail path is
correctly configured but has nothing to read.

- **`finance_sender_filter` is a comma-separated list now, defaulting to `burgan,on.com.tr`.**
  A bank's notification sender is frequently NOT its brand domain: ON mails come from
  `m.on.com.tr`, which the old single-value `from:burgan` could never match. That failure would have
  looked like an empty mailbox, not a misconfiguration.
- **Statement amounts carry THREE decimals**: `-140,000` is −140.00 TL, not −140000. Verified
  against the bank's own running-balance column (`-847,360` moves 3.383,070 → 2.535,710). Getting
  this wrong scales every figure by 1000. `finance_parser._to_float` already handles it.
- **The balance column is the best available oracle.** On the real 9-page export, 89/89 consecutive
  rows reconcile, and the newest row's balance minus its amount equals the header's stated closing
  balance exactly. Use it whenever validating a statement parser.
- **pdfplumber reports page 1 as 4 columns and pages 2-8 as 6** (same data, empty leading/trailing
  cell). Filtering on `len(cells) == 4` found 10 rows out of 91 — strip edge-empty cells first.
- **A row straddling a page break is emitted TWICE**, once with only the description prefix and once
  with the merchant. Identity must key on **(date, amount, balance)** and NOT the description: the
  running balance is unique per movement, so it separates two identical same-day purchases while
  collapsing the duplicate. Keying on description double-counted a transaction (−151.44).
- `parse_rows()` is split out from `parse_statement()` deliberately so the risky logic is testable
  without generating PDFs (which would mean adding `reportlab` for tests alone).
- **Statements need no LLM at all** — a row is a table cell with an explicitly signed amount, so
  direction is never inferred. This is the opposite of the mail path, where ambiguity must be refused.
- Live result: 90 transactions from one PDF, 0 rejections; the model itself drove
  `import_statement → export` **3/3** and reported the right figures.

## Follow-up turns are a different, weaker regime (2026-07-30)

The MVP works on the FIRST turn and degrades sharply on follow-ups. The mechanism is
architectural, not a prompt-tuning gap, and it explains a family of symptoms:

- **A tool result's next-step hint only influences the SAME turn.**
  `_compact_completed_turn_for_history()` keeps `[user message, tool summary, final answer]`
  in history — the **full tool result is deliberately dropped**. So every "name the exact next
  call" hint (which is what made the single-turn chain reliable) is invisible by the next turn.
  Adding `chart_kind` to `finance('export')` AND advertising it in the export's own result did
  not stop the model reaching for `plot_data` on "grafiği çizgi grafik yap" — measured twice.
  **Guidance that must survive a turn boundary belongs in the system prompt, not a tool result.**
- **The model imitates JARVIS's internal history marker.** A follow-up turn made **zero tool
  calls** and opened with a hand-written `[Tool execution summary: plot_data ok]`, then described
  a chart file that was never created. `strip_internal_markers()` (jarvis/agent.py) now removes a
  leading marker from user-facing replies — that stops a fabrication wearing a system badge, it
  does NOT stop the fabrication. Verifying file paths a response claims is the real fix; not built.
- **`plot_data` against the finance workbook is the reliable-failure hop.** Across runs the model
  used `x='Tarih'`/`y='Miktar'` (columns that do not exist), `y='Gelir,Gider'` (two columns in one
  field), the wrong sheet, and reported a path (`exports/...png`) different from where
  `generate_plot` actually wrote it (`data/runs/exec-*/`).

Practical consequence for the owner: **phrase the whole request in one sentence** ("... çizgi
grafik olarak") rather than asking for a modification afterwards.

## Known permanently-true gotchas

- `.env` is never committed (gitignored); `.env.example` is the template.
- `Jarvis.rar` (a ~116 MB local backup archive) sits in the repo root but is untracked/gitignored
  — safe to ignore, it's not part of the source tree.
- `data/` (ChromaDB, SQLite DBs, OAuth token caches, uploads) is entirely gitignored.
- `vault/conversations/*.md` (daily transcripts) are gitignored for privacy; the vault
  directory structure itself is tracked via `.gitkeep`.
- `tests/` (pytest, added Faz 8, extended in the 2026-07-15 GPT-5.6 remediation session and again
  through Agent Runtime rev.2) is the automated test suite — **927 tests as of 2026-07-22**
  (was 160 on 2026-07-15; this count moves fast — treat it as a snapshot, verify via
  `pytest --collect-only -q` before citing it), ~3 min, fully offline. Run `python -m pytest -q`
  from the repo root.
  **`pytest-timeout` is not installed** — passing `--timeout=` is a usage error (exit 4), which
  looks like a test failure but isn't. A handful are timing-sensitive and occasionally flake under
  full-suite load but always pass in isolation. **Never run the suite while a live-model harness
  (mvp_gate, manual_test_driver, a `--api` server driving Ollama) is running**: on 2026-07-30 a
  concurrent run took **3 h 14 m instead of 3 m 42 s** and failed 13 timing-sensitive tests
  (procedure_store/chroma, shell_workspace, todo background analysis) that all passed on a clean
  re-run. A wildly inflated wall-clock is the tell that the failures are contention, not code. Not exhaustive (most tool modules still have zero
  coverage) — extend incrementally rather than reintroducing throwaway scratch scripts for anything
  touching shared logic.
- **`asyncio.create_task()` only holds a *weak* reference to the returned task** — a task with no
  other referent (e.g. a bare `asyncio.create_task(coro())` whose result is never stored) is
  eligible for garbage collection before it finishes, silently killing it — no exception, no log,
  it just stops. Hit `graph/tools.py`'s `todo('add')` background prioritization this way (BUG-16,
  fixed 2026-07-15 via a module-level strong-reference set + a per-task done-callback to prune it
  after completion). Check any other fire-and-forget `create_task(...)` call in this codebase for
  the same pattern before assuming it's fine.
- **`.strip()` on whole `git status --porcelain` output corrupts exactly one filename.** Porcelain
  lines are `XY PATH`, and a modified-tracked file's line begins with a *space* (` M path`).
  Stripping the whole stdout removes that leading space from the **first line only**, so a fixed
  `line[3:]` slice silently eats one character of that one path — `.claude/settings.local.json`
  was reported as `claude/settings.local.json` (found 2026-08-05 while building the session
  hooks, by reading a real smoke run's output rather than trusting the code). Split on whitespace
  (`line.strip().split(None, 1)[1]`) instead of slicing a fixed prefix. Note also that git
  **collapses untracked directories** (`?? .claude/`), so an untracked dotfile does not reproduce
  this — only a tracked-and-modified one does, which matters when writing the regression test.
- **A temp git repo in a test still inherits the developer's GLOBAL git config.** This machine has
  a global ignore rule for `.claude/`, so `git add .claude/anything` inside a `tmp_path` repo
  fails with "ignored by one of your .gitignore files" — a test that passes for one person and
  fails for another, for reasons invisible in the test file. Neutralize it in the fixture
  (`git config core.excludesFile <nonexistent path>`) so fixture behaviour comes from the fixture
  alone. Same class as the `isolated_cwd` lesson: pin the environment or it will quietly decide
  your result.
