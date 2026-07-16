# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-16 — Stabilization sprint (Runtime Truth + Reset + Test Isolation)

**Context:** The owner ran a live manual-test session (same conversation, earlier) trying simple
requests against the running API — and it surfaced 7 real bugs: the model label shown in
`/status` was derived from the *requested* role, not whichever provider actually answered (a turn
served by the Ollama fallback still said "Gemini 2.5 Pro (Vertex, reasoning)"); local Ollama turns
were being priced as Gemini Flash; `POST /reset` 500'd on every content-bearing session; the test
run had written into the real `data/` directory; AI Studio's key turned out to be exhausted (429
prepayment-credits-depleted); Vertex was billing real money on every non-trivial turn despite the
local-first pivot's intent. These findings were handed to ChatGPT-5.6 (`GPT_Analysis.md`, owner's
desktop) for a second opinion — it confirmed the diagnosis, corrected two details (the `/reset`
crash only fires when `had_content=True`; reset archives, it doesn't delete), and wrote a 6-phase
stabilization plan with an explicit ordering: **fix measurement/runtime first, defer the
tool-domain-router and routing-policy work to a later sprint.** This session planned (2 Explore
agents + 2 offline LangChain probes to verify every claim against the live working tree, not just
trust the GPT analysis) and then implemented that plan inline, phase by phase, full pytest after
each phase — plan file: `.claude/plans/c-users-mertk-desktop-gpt-analysis-md-k-cuddly-castle.md`.

## What happened this session

**58 new tests, 218/218 pytest green, ruff clean.** See `CHANGELOG.md`'s "[Stabilizasyon
sprinti...]" entry for the full per-phase writeup (file:line references, exact mechanism per fix).
Summary:

1. **Faz 1 — JARVIS_HOME isolation root.** New `jarvis/paths.py`. Unset `JARVIS_HOME` = today's
   cwd-relative behavior, byte-identical. Set = every runtime store (sessions.db, checkpoints,
   usage.json, audit_log, kill_switch, ChromaDB, vault, uploads, tool caches) AND the
   project-root-anchored Gmail/Calendar/email_triage OAuth tokens (a plain `chdir` would miss
   these) redirect under it. ~25 call sites rewired. New `tests/conftest.py` `jarvis_home` fixture.
2. **Faz 2 — `POST /reset` 500 fix.** Root cause: `api.py` ran the whole sync `reset()` inside
   `run_in_executor` (worker thread, no event loop), and `reset()` internally called
   `asyncio.create_task()` — `RuntimeError`, on every content-bearing session, always. Split into
   `_reset_state_sync()` (pure, thread-safe) + `async reset_async()` (`asyncio.to_thread` for the
   state mutation, summary scheduled only after control returns to the loop). Guard added so a
   loopless caller degrades to "summary skipped, log it" instead of crashing. All 3 call sites
   (`api.py` ×2, `cli.py`) moved to `reset_async()`.
3. **Faz 3 — Provider invocation trace (runtime truth).** Every tier in `providers/__init__.py`
   now stamps its own identity via `with_config(metadata={...})`. New `jarvis/llm_trace.py`
   (`LlmTraceRecorder`) turns real callback events into one `LlmCallTrace` per LLM call; the model
   label (`_cloud_model`) and `/status`'s new `requested_role`/`actual_provider`/`actual_model`/
   `fallback_used` fields now come from this, not a role-derived guess. **Found and fixed two of
   its own regressions along the way**: `background_turn()` referenced a `use_pro_agent` local that
   was never assigned (only written into a state dict) — raised `NameError` inside a background
   task whose exception nobody awaited, which made an unrelated test hang for 11+ real minutes
   before I traced it; and the same method's Faz-4 usage wiring added a `self.usage` read that
   `test_background_turn.py`'s `_FakeAgent` didn't have, reproducing the identical hang shape.
   Both fixed; **if you see a pytest run stall with no forward progress, check for exactly this
   pattern first** — an unawaited background-task exception plus a test blocked on an unrelated
   `asyncio.Event` that never fires is now a proven failure mode in this codebase, twice.
4. **Faz 4 — Provider-aware usage v2.** `usage.py`'s `record()` now takes `(provider, model,
   tokens_in, tokens_out, billable)` — cost comes from the caller-declared `billable` flag, not a
   `"pro" in model_id` guess (the exact reason local Ollama turns were priced as Gemini Flash).
   `flash_turns`/`pro_turns` now count billable Vertex calls only (correct for gcp_quota.py's RPD
   tracking); new additive `by_provider` breakdown. Removed the double-counting
   `_record_usage_from_result` (rescanned ALL messages every turn) and `chat_stream()`'s `len//4`
   estimate — the recorder's `on_llm_end` is now the single writer, and background/proactive turns
   record real usage for the first time.
5. **Faz 5 — `CLOUD_POLICY` gating.** New `Settings.cloud_policy: Literal["off","explicit","auto"]
   = "off"` — **default is `off`**, the owner's live-tested decision. `off` = zero cloud calls,
   anywhere, no exceptions (including a manual pin). `explicit` = only a manual `/model` pin passes
   (found and fixed a subtlety here: the Vertex-pinned path delegates through `_cloud_tiers`, which
   needed its own `pinned` parameter threaded through — otherwise `explicit` policy blocked even an
   explicit pin). `auto` = pre-sprint behavior, unchanged (existing `.env` files add
   `CLOUD_POLICY=auto` to keep current behavior). The 8 direct-Gemini modules not yet migrated onto
   the provider router (`fact_extractor`, `entity_extractor`, `finance_extractor`,
   `session_summarizer`, `todo_analyzer` ×2, `email_triage`, `pdf_vision`, `deep_research`) got a
   narrow early-return gate each — `note_degraded(feature)` + `/status.degraded` visibility instead
   of a silent empty return. **Gating only, not migration** — deliberately narrow scope.
6. **Faz 6 — `--profile test` + `EXTERNAL_WRITES_ENABLED`.** `__main__.py` gained `--profile
   {default,test}`; `test` is detected via an argv pre-scan *before* `load_dotenv()` runs (module-
   level ordering constraint), skips it entirely, sets `JARVIS_SKIP_DOTENV=1` (which `config.py`'s
   own independent `env_file` reader also now honors — there were two separate .env-reading paths,
   both needed closing), seeds a fresh `tempfile.mkdtemp()` `JARVIS_HOME` (unless already set),
   and forces `CLOUD_POLICY=off` + `EXTERNAL_WRITES_ENABLED=false`. New
   `Settings.external_writes_enabled` (default `True`) enforced in `make_confirmation_node`
   (`graph/nodes.py`) — any `side_effect_type=="external_write"` tool call (gmail/calendar/drive/
   itu_mail/spotify) is hard-denied before the interrupt, same shape as the kill switch, narrower
   scope. Fixed a real bug found live while smoke-testing this: the startup banner used a `⚠`
   character that crashed on launch under Windows' legacy console renderer (triggered specifically
   by redirected/piped stdout, e.g. into a log file) — `UnicodeEncodeError` on `cp1254`. Banner is
   now plain ASCII.

## Live verification performed (not just unit tests)

Ran `python -m jarvis --api --profile test --port 8130` for real and drove it with curl:
- `/health` → ok.
- `/status` before any turn → fresh session, `cloud_policy:"off"`.
- `POST /chat {"message":"merhaba"}` → answered, `qwen2.5:7b-instruct (Ollama, local)`.
- `/status` after that turn → `actual_provider:"ollama"`, `actual_model:"qwen2.5:7b-instruct"`,
  `fallback_used:false`, **`session_cost_usd:0.0`** (previously would have shown a nonzero Gemini
  price for this exact same local turn).
- `POST /reset` on that content-bearing session → **HTTP 200** `{"ok":true,...}` (previously 500,
  always). New session_id afterward.
- `/status` after reset → `degraded:["session_summarizer"]` — proof the reset's fire-and-forget
  summarization attempt hit the `CLOUD_POLICY=off` gate and logged itself as degraded instead of
  silently trying (and failing, or worse, quietly succeeding) a real Gemini call.
- MD5 hash of all 58 files under the real `data/`+`vault/`, taken before and after the entire
  smoke test: **byte-identical**. The isolation actually holds under a real running server, not
  just in mocked unit tests.

## Explicitly deferred / not this session's scope

- **Tool-domain router** (turn-scoped 5-8 tool subset) — GPT's plan's own "sprint 2", explicitly
  deferred. This is the actual fix for the live-test session's original finding that
  qwen2.5:7b-instruct's tool-call rate degrades sharply with the full ~30-tool/5400-token system
  prompt (5/6 → 3/4 → effectively 0/7 as tool count and prompt size grew in the pre-sprint manual
  test). Not attempted here — this sprint only made the *measurement* of that problem trustworthy.
- **`_is_trivially_simple()`'s cloud-first default** (`agent.py`) — still routes any non-trivial
  query to the `reasoning` role by default, and still has the substring false-positive bug
  (`"ok"` matches `"oku"`/`"okul"`/`"çok"`, `"hi"` matches `"hiçbir"`/`"tarihi"`, `"hey"` matches
  `"heyecanlıyım"`) found live in the same test session. `CLOUD_POLICY=off` makes this
  *practically* moot right now (routing to "reasoning" just gets pure Ollama anyway), but the
  function itself is unchanged — sprint 2's scope per the GPT plan.
- **Critic's revision-instruction injection** (`graph/nodes.py`'s `critic_node` appends its
  feedback as a synthetic `HumanMessage`) — a small model can mistake this for a real user message
  ("Thank you for the feedback...", observed live pre-sprint). Not touched; sprint 2 scope.
- **The 8 direct-Gemini modules' full migration** onto a shared provider gateway — gated (Faz 5
  above), not migrated. Sprint 3 per the GPT plan.
- **2 orphaned `Settings` fields** (`drive_cache_dir`, `geo_math_output_dir`) — nobody reads them;
  the corresponding modules (`tools/drive.py`, `tools/geo_math_tool.py`) use their own
  JARVIS_HOME-aware functions instead. Not wired this sprint (found during Faz 1's path audit,
  documented, left alone — wiring them is a 2-line change if ever needed, but wasn't in scope).
- **Voice model caches** (`~/.cache/jarvis`, HF hub, marker-pdf) — deliberately NOT moved under
  JARVIS_HOME; they're immutable multi-GB downloads, re-fetching them per isolated test run would
  be actively harmful, not safer.
- **`purge_session`** (an actual-delete companion to `reset`'s archive-only semantics) — the GPT
  plan explicitly excluded this from the current sprint.
- **The 4 remaining worktree branches** from an earlier session (`claude/eager-noether-46af01`,
  `claude/gifted-wilbur-e021ea`, `claude/stoic-spence-2c5246`, `claude/thirsty-mclean-f67665`) —
  untouched, still pending a read-through per multiple previous HANDOFFs' recommendation.
- **Committing/pushing** — nothing was committed automatically (project's standing rule: only
  commit when explicitly asked).

## Git state as of this session

- Branch: `langgraph-migration`. **Two sessions' diffs are stacked, uncommitted**: the prior
  session's GPT-5.6 remediation work (Faz 1-7 of *that* plan — API secure-by-default, proactive
  read-only, procedure provenance, MCP/SSRF hardening, concurrency isolation, shell/python guard,
  observability/CI) was already uncommitted when this session started, and this session's
  stabilization-sprint work landed on top of it. `git status` currently shows ~55 modified files
  and ~20 new files combined across both sessions.
- New files from *this* session specifically: `jarvis/paths.py`, `jarvis/llm_trace.py`,
  `tests/test_jarvis_home.py`, `tests/test_reset_lifecycle.py`, `tests/test_llm_trace.py`,
  `tests/test_cloud_policy.py`, `tests/test_profile_test_entrypoint.py`.
- No `.env`, real `data/`, real `vault/`, or OAuth credential files touched or added — verified by
  hash comparison (see "Live verification" above), not just assumed.

## Recommended next steps (pick up here)

1. **Review and commit/push both sessions' stacked diff** — `git status`/`git diff` first; consider
   whether to split into two commits (prior session's security hardening vs. this session's
   stabilization sprint) or one, since neither was committed when the other started.
2. **Sprint 2 (per the GPT plan): Local Routing + Tool Capability Router + Critic Separation.**
   Now that the measurement layer is trustworthy (Faz 1-6 above), this is the actual fix for why
   manual testing showed qwen2.5:7b-instruct failing to call tools reliably once the real system
   prompt + full tool set were in play. Rough shape from the GPT plan: a `capability_router` node
   that narrows the tool set to ~1-6 relevant tools per turn (`tool_choice="required"` when exactly
   one applies), rewrite `_is_trivially_simple()` to actually default local-first, fix the substring
   false-positive matching, and stop injecting critic feedback as a fake `HumanMessage`.
3. **Sprint 3: migrate the 8 direct-Gemini modules** onto a shared `ModelGateway`-style API instead
   of each constructing `ChatGoogleGenerativeAI` directly — the GPT plan sketches `structured`/
   `summarize`/`vision`/`research` roles, defaulting `structured`/`summarize` to local-only.
4. Re-verify AI Studio's key status before ever setting `CLOUD_POLICY=auto` for real use — it was
   confirmed exhausted (`429 RESOURCE_EXHAUSTED`, prepayment credits depleted) during this session's
   diagnosis; `auto` policy would otherwise silently fall through to Vertex billing on every
   escalated turn, the exact problem this sprint responded to.
5. Consider whether `flash_turns`/`pro_turns`' new "billable Vertex calls only" meaning should be
   surfaced anywhere in the CLI/HUD explicitly (currently only documented in code comments +
   CHANGELOG) — `gcp_quota.py`'s RPD-quota displays consume it correctly, but a user reading
   `/budget` output has no obvious cue that these two counters no longer include Ollama activity.
6. Read the 2 potentially-unique worktree branches (`gifted-wilbur`'s eval suite, `stoic-spence`'s
   rolling summarization) — carried over from multiple previous sessions' recommendation, still
   untouched.

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
pytest                                # 218 tests, ~65s, fully offline
ruff check jarvis/ tests/             # should be clean
ollama serve                          # confirm it's up: curl http://localhost:11434/api/tags
python -m jarvis                      # CLI (real .env, real data/ — normal use)
python -m jarvis --api --profile test --port 8130   # isolated smoke-test path, see above
```

New env vars this session (all optional, `.env.example` documents each): `CLOUD_POLICY` (unset =
`off`, add `CLOUD_POLICY=auto` to `.env` to restore pre-sprint cloud-routing behavior),
`JARVIS_HOME`/`EXTERNAL_WRITES_ENABLED` (process-env only, meant for `--profile test`'s automatic
use, not a real `.env`). No new required dependencies.
