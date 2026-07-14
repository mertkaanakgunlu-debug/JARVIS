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

## Known permanently-true gotchas

- `.env` is never committed (gitignored); `.env.example` is the template.
- `Jarvis.rar` (a ~116 MB local backup archive) sits in the repo root but is untracked/gitignored
  — safe to ignore, it's not part of the source tree.
- `data/` (ChromaDB, SQLite DBs, OAuth token caches, uploads) is entirely gitignored.
- `vault/conversations/*.md` (daily transcripts) are gitignored for privacy; the vault
  directory structure itself is tracked via `.gitkeep`.
- No automated test suite exists in `jarvis/` — `CONTRIBUTING.md`'s implied "run tests"
  step has nothing to run today.
