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
- **No Node.js/npm on PATH** (confirmed 2026-07-14, Faz 3) — neither Bash nor PowerShell can find
  `node`, and the Electron app's own bundled `node_modules/electron`/`esbuild.cmd` couldn't be
  coaxed into a standalone interpreter either. Any Electron (`electron/`) JS/JSX change made by a
  Claude Code session cannot be syntax-checked or run in this environment — it needs the owner's
  own `npm run dev`/build step before being trusted. `.venv`'s Python is unaffected.
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
  orchestrator is kept at `jarvis/legacy/agent_pydantic.py` for reference only — it is not
  imported by any live path, and its own prompt-loading logic is independently broken
  (points at a `jarvis/legacy/prompts/system.md` that doesn't exist). Don't resurrect it
  without fixing that path first.
- The 5 sub-agents (math, writer, research, coder, geomath) are still pydantic-ai, bridged
  into the LangGraph tool layer via a `_run_coro` helper. Migrating them to native LangGraph
  sub-graphs is tracked as Phase 8 — not started.
- System prompt was modularized (2026-05-24, Phase 1) from one `system.md` into 6 concern
  files under `jarvis/prompts/core/` assembled by `jarvis/prompts/prompt_loader.py`. The old
  `jarvis/prompts/system.md` still exists as a dead pointer file (says so in its own header)
  — safe to delete once nobody's relying on it as documentation.
- `jarvis/prompts/core/05_memory_policy.md` and `06_context_injection.md` have their names
  and contents swapped relative to what you'd expect (05 = timezone/user-context rules,
  06 = the actual memory/entity/todo injection blocks). Known, not yet fixed — check both
  files if you're looking for either topic.
- ToolSpec risk metadata (`jarvis/tool_registry.py`, Phase 2) and the confirmation gate
  (`jarvis/graph/nodes.py`, Phase 3) both shipped 2026-05-24 — see [CLAUDE.md](CLAUDE.md)'s
  safety section for why the gate doesn't yet do what its name implies.
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

## Known permanently-true gotchas

- `.env` is never committed (gitignored); `.env.example` is the template.
- `Jarvis.rar` (a ~116 MB local backup archive) sits in the repo root but is untracked/gitignored
  — safe to ignore, it's not part of the source tree.
- `data/` (ChromaDB, SQLite DBs, OAuth token caches, uploads) is entirely gitignored.
- `vault/conversations/*.md` (daily transcripts) are gitignored for privacy; the vault
  directory structure itself is tracked via `.gitkeep`.
- No automated test suite exists in `jarvis/` — `CONTRIBUTING.md`'s implied "run tests"
  step has nothing to run today.
