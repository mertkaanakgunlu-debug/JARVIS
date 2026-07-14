# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-14 (same-day continuation, second phase)

**Context:** Picked up directly from this same day's earlier session, which had just finished
Faz 0 (memory-critical stabilization) and left "Start Faz 1" as the top recommended next step.
This session implemented Faz 1 — local-first brain + model router.

**What happened this session — Faz 1 complete:**

- **New `jarvis/providers/` module** — `get_llm(role, settings, *, tools=, max_output_tokens=)`
  resolves a role (`fast`/`local`/`realtime`/`reasoning`) to a concrete LangChain chat model.
  `graph.py`'s old hardcoded `make_llm_fast`/`make_llm_pro` (`ChatGoogleGenerativeAI` factories,
  lines 49-93) are gone — `build_graph()` now calls `get_llm("fast", settings, tools=tools)` and
  `get_llm("reasoning", settings)`. `agent.py`'s complexity-based Flash/Pro pick
  (`_is_trivially_simple`/`use_pro_agent`) is unchanged in logic — it already was the
  capacity-routing mechanism the plan asked for — it now just selects between the local (fast)
  and cloud (reasoning) roles instead of Flash vs. Pro.
- **Ollama wired as the real local-first primary**: `fast`/`local`/`realtime` roles → `ChatOpenAI`
  against Ollama's OpenAI-compatible endpoint (`qwen2.5:7b-instruct`), ahead of cloud, with a real
  `.with_fallbacks()` safety net to whichever cloud tiers are configured. `reasoning` → configured
  cloud tiers (`[Vertex Pro, AI Studio Gemini Flash]`) with **local Ollama as its own final
  fallback** — nothing is cloud-mandatory anymore, including escalation. AI Studio's target is
  deliberately `cloud_model_fallback` (`gemini-2.5-flash`, 1500 RPD free), not `cloud_model_pro`
  (`gemini-2.5-pro`, 25 RPD free).
- **`nomic-embed-text` via Ollama wired in `jarvis/memory.py`** (`_build_ollama_ef`) as the
  preferred embedding function for `jarvis_docs`/`jarvis_summaries`, ahead of the existing Gemini
  embedder, with the pre-existing EF-conflict `except ValueError` fallback preserved so
  already-embedded collections keep whatever EF they were created with.
- **[BUG-24] fixed:** removed the dead `cloud_tier == "pro"` branch in `config.py`'s
  `effective_cloud_model` (that value is never actually set anywhere).
- **[BUG-22] fixed:** `switch_model()` now persists `new_settings` onto a new
  `JarvisAgent._effective_settings` attribute; the quota-fallback rebuild in `chat()` copies that
  instead of construction-time `self.settings`, so it no longer silently discards a prior manual
  model switch.
- **Manual `/model` switching preserved**: added `Settings.pin_cloud_model` (set by
  `switch_model()`) so an explicit cloud pick still bypasses the local-first default for the fast
  role exactly as before Faz 1 (reasoning role is intentionally never affected by the pin, same as
  pre-Faz-1). Added a `local/qwen2.5-7b` entry to `AVAILABLE_MODELS` / `/model` menu / the natural
  -language keyword table so there's an explicit way back to the local-first default after pinning.
- **Per-turn model label fixed**: `agent.py`'s `_cloud_model`/`current_model_label` (shown in the
  CLI panel title, `/status`, and the API's `model` field) now reflects which role actually served
  the *most recent* turn (`_last_turn_used_pro`, new) instead of a single static label — it used to
  always show the configured cloud model even when Flash/Pro never ran that turn; now local-first
  turns honestly show `qwen2.5:7b-instruct (Ollama, local)` etc. `_HudEventCallback.on_llm_start`
  also now tags HUD events `"local"` vs `"cloud"` correctly instead of always `"cloud"`.
- **Quarantined** (header note, not deleted) the local-model path in
  `jarvis/legacy/agent_pydantic.py` — points at `jarvis/providers/get_llm()` as the superseding
  implementation. Full retirement of `jarvis/legacy/` is still Faz 8, not this phase's.
- **Two bonus bugs found live while implementing/verifying the above (not in the original plan):**
  - A latent `AttributeError`: the pre-Faz-1 `make_llm_fast()` called `.bind_tools()` on the
    result of `.with_fallbacks()`; `RunnableWithFallbacks` has no `bind_tools` (confirmed:
    `hasattr(RunnableWithFallbacks, "bind_tools")` is `False`). Never hit in practice only because
    recent runs had `use_vertex=False`. Fixed by having `get_llm()` bind tools before wrapping.
  - A construction-time crash on a credential-less config: `ChatGoogleGenerativeAI`'s constructor
    validates (pydantic) that an API key is present, so eagerly building every cloud tier crashed
    `build_graph()` itself the moment `GEMINI_API_KEY` was empty and Vertex wasn't configured —
    i.e. exactly the "pure local, no cloud credentials" setup this phase exists to enable. Fixed:
    every cloud tier now goes through `_safe_construct()`, which drops (logs) a tier that can't
    even construct instead of raising.
- Full detail + exact verification performed is in [ROADMAP.md](ROADMAP.md)'s Faz 1 section.

**Environment gap found AND fixed this session (owner approved):** this dev machine started with
**zero working model tiers** (Ollama not installed, Vertex ADC missing, AI Studio 429ing). Owner
said to proceed with the necessary downloads, so: installed Ollama (`winget install Ollama.Ollama`),
pulled `qwen2.5:7b-instruct` (4.7 GB) and `nomic-embed-text` (274 MB), and got the server running
cleanly. **Gotcha hit along the way:** the Windows installer's own auto-started tray service and a
first manual `ollama.exe serve` launch raced for port 11434 — the loser left a listener that
accepted TCP connections but never answered HTTP requests (hung, not refused — easy to misdiagnose
as a code problem). Fixed by killing all `ollama*` processes and starting exactly one instance via
`Start-Process`. See [MEMORY.md](MEMORY.md) for the full note if this recurs.
Vertex ADC is still missing (unfixed, not asked — optional, everything works without it now).
AI Studio's `gemini-2.5-flash` turned out to be **intermittently rate-limited, not permanently
dead** — repeat testing minutes apart showed it both succeeding and 429ing with the same
"prepayment credits depleted" message; don't over-conclude "this key is dead" from one failed call.

**Verification performed (all safe/isolated — no test suite exists in-repo):** router unit checks
(every role/tools/pin combination resolves to the expected model type); a live `.invoke()` against
each real tier individually to characterize behavior; full `JarvisAgent`/`build_graph()`
construction in **isolated temp dirs** (per the isolate-test-data-paths lesson from Faz 0 — never
the real project's `data/`/`vault/`), both with the real `.env` and with none at all (the
credential-less case that used to crash, now fixed); **a real end-to-end `chat()` turn, which
succeeded and returned the correct `qwen2.5:7b-instruct (Ollama, local)` label**; direct
`.bind_tools()` tool-calling confirmed on the local model (a `get_weather` tool call, correctly
invoked with the right args); a complex-query turn to exercise the `reasoning` role path. Plain
`import jarvis.<module>` checks on every touched file.

## Git state as of this session

- Branch: `langgraph-migration`, **not merged to `main`**.
- Uncommitted (beyond Faz 0's still-uncommitted changes from earlier today): new
  `jarvis/providers/__init__.py`; modified `jarvis/graph/graph.py`, `jarvis/agent.py`,
  `jarvis/config.py`, `jarvis/memory.py`, `jarvis/cli.py`, `jarvis/tools/indexer.py`,
  `jarvis/legacy/agent_pydantic.py` (header note only), `requirements.txt` (added
  `langchain-openai`), plus `HANDOFF.md`/`MEMORY.md`/`ROADMAP.md`/`CHANGELOG.md` doc updates.
  **Nothing has been committed yet this session either** — not asked.
- `langchain-openai` was installed into `.venv` via `uv pip install` (not yet reflected in a
  commit, but already active in the venv — `requirements.txt` has the matching entry).
- No stashes. Still 21 stray `.claude/worktrees/*` directories from past sessions, still
  untouched (still needs explicit go-ahead — destructive, Faz 8 territory anyway).

## Recommended next steps (pick up here)

1. **Decide whether to commit** — both today's Faz 0 changes AND this session's Faz 1 changes are
   sitting uncommitted (wasn't asked either time). If committing, consider whether to do it as one
   combined commit or two separate ones matching the phase boundary.
2. **Start Faz 2** ([ROADMAP.md](ROADMAP.md)) — 5-layer cognitive memory (semantic → procedural →
   meta), the owner's #1 priority. Faz 1's local embedding wiring feeds directly into this.
3. Ollama now needs to be running (`ollama serve`, or just launch the Ollama app from the Start
   Menu — it auto-starts its own background service) before `python -m jarvis` for the local-first
   path to actually be used; if it's not running, everything still works via the cloud fallback.
   If Ollama seems to hang (connects but never responds), see the port-11434-race gotcha in
   [MEMORY.md](MEMORY.md) — kill all `ollama*` processes and start exactly one instance.
4. Optional, not blocking anything: `gcloud auth application-default login` if Vertex Pro/Flash
   access is wanted (AI Studio Flash + local Ollama both already work without it).
5. Confirm whether the 21 stray worktrees/branches should be cleaned up (still deferred, still
   needs your go-ahead — destructive).

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
ollama serve                                          # or launch the Ollama app — local brain,
                                                       # now installed with qwen2.5:7b-instruct +
                                                       # nomic-embed-text already pulled
gcloud auth application-default print-access-token    # optional — only for Vertex Pro/Flash;
                                                       # still missing on this machine, not required
python -m jarvis                                      # or --api / --voice / --monitor
```

If `pip install -r requirements.txt` hits `resolution-too-deep`, use `uv pip install -r
requirements.txt --python .\.venv\Scripts\python.exe` instead (Google Cloud SDK's loose transitive
pins — same issue Faz 0 hit).
