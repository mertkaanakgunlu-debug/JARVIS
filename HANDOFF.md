# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-15 — Faz 7 (Proaktiflik — software half)

**Context:** Owner said "Faz 6 ile devam et" (continue with Faz 6). ROADMAP.md marks Faz 6 (Fiziksel
Dünya / IoT) `⛔ deferred` — hardware-gated (Zigbee coordinator dongle + a Home Assistant instance,
owner has only an RP2040) — and the prior session's own HANDOFF explicitly recommended Faz 7 as the
legitimate next candidate if Faz 6 stayed blocked. Asked and confirmed: owner picked Faz 7 (not
"I have the hardware now," not "do the HA-container prep anyway"). Second question — Faz 5 was
still fully uncommitted from the prior session (19 files) — owner picked "commit Faz 5 first,"
matching the established one-phase-per-commit pattern, so it was committed (`eaa9c25`) before any
Faz 7 diff started.

## What happened this session

Faz 7's own ROADMAP text splits cleanly into a software half (no hardware needed) and a sensor half
(needs Faz 6's MQTT). Only the software half was in scope this session — MQTT stays untouched.

1. **New `JarvisAgent.proactive_turn()`** (`jarvis/agent.py`) — the actual "give monitor.py a path
   into agent.chat()" deliverable. Runs the exact same compiled graph `chat()`/`chat_stream()` do
   (same tools, same `policy_guard`/kill-switch/audit_log gate — **zero changes to any of them**,
   same principle Faz 5's MCP layer already established: a new traffic source into one existing
   choke point, not a reason to add a second one), but on an **isolated** message list
   (`[SystemMessage(_proactive_system_prompt(...)), HumanMessage(prompt)]`) and a dedicated
   LangGraph thread_id — deliberately never touches `self._history`/`_turn`/
   `session_store.save_turn` or episodic memory, so JARVIS's background "should I say anything?"
   self-talk can never leak into the user's real conversation history or get replayed as prior
   context on their next real turn. Still takes `_state_lock` (Faz 0 / BUG-8) like every other entry
   point. Returns a new `ProactiveOutcome(kind="none"|"response"|"needs_confirmation", text=, tools=)`
   dataclass rather than raising/returning text directly, so `monitor.py` can react to each case
   distinctly without string-sniffing.
2. **Never raises `ConfirmationRequired`** — there's no interactive channel for a background thread
   to answer one (same constraint `TaskExecutor._run()` already documented). A `GraphInterrupt` is
   caught, the pending confirmation is **discarded** (never stored in `self._pending_confirmations`,
   never resumable — deliberately not reusing the `POST /chat/confirm/{conf_id}` + `event_bus`
   plumbing, since per `docs/SAFETY.md` no UI actually consumes that event yet; a "resumable but
   nothing resumes it" confirmation would just leak forever) and reported back as
   `kind="needs_confirmation"`. This is the concrete "confirm-or-notify, not silent execution"
   mechanism the ROADMAP's Faz 7 text asked for.
3. **`monitor.py` wiring**: `JarvisMonitor.__init__` gained an optional `agent: JarvisAgent | None`
   param (`None` in the standalone `python -m jarvis --monitor`-only mode, which stays deliberately
   agent-less — confirmed unchanged behavior there). New `_maybe_proactive(prompt, source)` bridges
   from the monitor's own daemon thread to `agent.proactive_turn()` via `asyncio.run(...)` — the
   same synchronous-thread-to-fresh-loop pattern `TaskExecutor._run()` already uses, not a new
   concurrency primitive. Called from `_check_email()`/`_check_calendar()` **alongside** (not
   instead of) the existing unconditional toast. Off by default
   (`Settings.monitor_proactive_enabled=False` — existing toast-only behavior is completely
   unaffected until explicitly turned on) and throttled across all sources combined
   (`monitor_proactive_min_gap_sec`, default 600s) so a burst of unread emails after being offline
   can't queue many LLM calls at once.
4. **`[monitor-in-api]`** — `--monitor` was previously silently ignored under `--api` (only
   `cli.py`'s branch ever constructed a `JarvisMonitor`; tracked as an explicit "Known gap" in
   `docs/ARCHITECTURE.md` since the original refactor backlog, and literally named
   "monitor-in-api...subsumed...into Faz 7" in ROADMAP.md's closing note). `api.py`'s `lifespan()`
   now starts one (with `agent=_agent`) when `run_server(..., monitor=True)`; `__main__.py`'s
   `--api` branch now threads `args.monitor` through. This matters more than the CLI case in
   practice — the API server is the long-running process a phone/HUD actually talks to.
5. **[BUG-19] fixed** — budget/GCP quota alerts had no dedup at all and re-fired the identical toast
   every poll cycle for as long as a condition stayed over-threshold.
   `gcp_quota.quota_alert_check()` now returns `(alert_key, message)` pairs instead of bare strings
   (the message text embeds live numbers that change every call — e.g. a fluctuating RPM percentage
   — so a text-based dedup wouldn't actually dedup). `_check_gcp_quota()` dedups per day (RPM/credit
   conditions are a recurring daily signal — permanently suppressing after the first alert would
   hide a real problem on day 2); `_check_finance()`'s budget-threshold loop dedups per
   `(year, month, category)` (naturally self-clears at the start of each new month, matching how a
   monthly budget actually resets — no explicit reset code needed).
6. **A real, live finding during verification — caught, mitigated, honestly documented, NOT fully
   closed:** a real `proactive_turn()` call against local `qwen2.5:7b-instruct`, given a mundane
   calendar-event trigger, hallucinated an unrelated `procedure_save` tool call (fabricated a
   "budget_chart_report" procedure that had nothing to do with the prompt). `procedure_save` is
   `risk_level=2` (`local_write`, `requires_confirmation=False`) — by `policy_guard`'s own
   pre-existing, deliberate design ("kill switch is L3-only"), L2 writes execute without
   confirmation for a normal human-driven turn, where a person is present to notice and
   course-correct. A background self-check has nobody watching, so this is a real gap Faz 7 newly
   *exposes* (the L2-no-gate design isn't new; being reachable with nobody watching is).
   **Mitigated**: `_proactive_system_prompt()` now explicitly forbids calling any
   creating/saving/sending/modifying tool during a proactive check — investigation must stay
   read-only, a suggested action belongs in the reply text, not a live tool call. Re-ran the
   identical trigger against the same model after the prompt change: the hallucinated tool call did
   not reproduce (the model gave a text-only, if slightly awkward, response instead — see
   Verification below). **This is a prompt-level mitigation on a non-deterministic model, not a
   structural guarantee the way the L3 gate is one** — narrowed, not closed. A real structural fix
   (a separate, read-only-only tool set for proactive turns, via its own `build_graph()` call) would
   close it properly; not built this session — real added complexity (a second compiled graph to
   keep in sync with the main one on every model-switch/MCP-connect) for a feature that ships fully
   off by default. Documented in `docs/SAFETY.md`'s new "What Faz 7 changed" section, `MEMORY.md`,
   and `CLAUDE.md`'s safety paragraph — **read `docs/SAFETY.md` before assuming proactive turns are
   as safe as interactive ones.**
7. **Docs brought fully in sync**: `ROADMAP.md`'s Faz 7 section (bullets checked off, full verify
   writeup, phase-overview table row), `docs/ARCHITECTURE.md` (new "Proactive monitoring" section +
   the `JarvisMonitor`-in-`--api` Known-gaps row flipped to done), `docs/SAFETY.md` (new "What Faz 7
   changed" section with the honest L2 residual-risk writeup), `MEMORY.md` (design-decisions section
   mirroring the Faz 4/5 ones, same residual-risk detail), `CLAUDE.md` (safety-model paragraph +
   "what's still genuinely not done" bullet), `CHANGELOG.md` (new entry), `.env.example` (new
   `MONITOR_PROACTIVE_ENABLED`/`MONITOR_PROACTIVE_MIN_GAP_SEC`, documented — note: no other
   pre-existing `monitor_*` setting was previously documented in `.env.example` either; only the two
   new ones were added, not a retroactive full pass).

## Verification performed

**Isolated, stub-agent tier (15/15 checks, no real LLM, no real `data/` access)**: `agent=None` and
`monitor_proactive_enabled=False` are both true no-ops (zero behavior change to existing
toast-only monitoring, confirmed by call-count assertions on a stub `proactive_turn`); enabled +
`kind="none"` calls the agent but stays silent; enabled + `kind="response"`/`"needs_confirmation"`
fire the correct, distinct toast (and the confirm-needed toast correctly names the gated tool,
proving it did NOT silently execute); the cross-source throttle blocks a second call inside the gap
window and allows one through once the gap has elapsed (simulated via rewinding the internal
timestamp, not a real sleep); GCP-alert and budget-alert dedup each collapse 3 synthetic poll
cycles (with intentionally *changing* message text, mimicking real fluctuating percentages) into
exactly 1 toast.

**Real-`JarvisAgent` tier, isolated temp cwd** (per MEMORY.md's isolate-test-data-paths lesson —
loaded the real `.env` first via an absolute path, *then* `os.chdir()`'d into a fresh temp dir
before constructing anything, so pydantic-settings' env-vars-beat-dotenv resolution order survives
the chdir): a deterministically-mocked `GraphInterrupt` (constructed via real
`langgraph.errors.GraphInterrupt`/`langgraph.types.Interrupt`, not a hand-rolled stand-in) is caught
by `proactive_turn()` and reported as `kind="needs_confirmation"` with the correct pending tool
name; the pending confirmation is confirmed **absent** from `agent._pending_confirmations` (nothing
will ever resume it — matches the design); `_state_lock` is confirmed released, not deadlocked. A
**real local-LLM** (`qwen2.5:7b-instruct` via Ollama, started fresh this session — was not running
at session start) proactive turn against a mundane calendar trigger completes without touching
`self._history`/`_turn`/the session store's saved turn index (checked before/after via direct
equality/`last_turn_idx()` comparison) — this is what caught finding #6 above on the first run (the
hallucinated `procedure_save`, isolated to the temp dir, never touched real data) and confirmed the
fix on a second run (no hallucinated tool call; the model instead replied with a text-only, somewhat
clarifying-question-shaped response rather than a crisp "nothing to do"/suggestion — a genuine,
if minor, response-*quality* observation for local 7B on this meta-task, separate from the
tool-call-safety finding, and part of why this feature ships off by default). A real local-LLM turn
given an explicit "call gmail with action=send" instruction did not attempt the gated tool call this
particular run (observational, not a pass/fail — depends on the model's own judgment call) — the
mechanism itself (deterministic `GraphInterrupt` handling) was already proven by the mocked test
above.

**Real product, real data, both entry points** (`python -m jarvis`, real `data/` — not isolated,
since these are the actual daily-use commands, matching Faz 4/5's own established verification
tier): plain CLI (no `--monitor`) starts and exits cleanly on EOF, no exceptions from any Faz 7 code
path. Standalone `python -m jarvis --monitor` (agent-less path — `__main__.py`'s separate branch,
confirmed still unaffected since it never passes `agent=`) starts cleanly, runs, shuts down on
timeout with no traceback. `python -m jarvis --api` starts and answers `/health` correctly both
**without** `--monitor` (regression check on `lifespan()`'s new branch being correctly skipped) and
**with** `--monitor` (the new path — `JarvisMonitor` construction + `.start()` inside `lifespan()`
against the real agent/scheduler/todo_store, confirmed alive and healthy ~19s after startup, clean
process shutdown, no leftover processes). Confirmed the real `.env` was never modified and
`MONITOR_PROACTIVE_ENABLED` is unset there (defaults to `False` in code) — none of these real-data
runs made any autonomous LLM call or touched anything beyond the pre-existing read-only Gmail/
Calendar state-init calls monitor.py has always made.

**Not exercised live this session**: the `cli.py` `run()` code path's specific `agent=agent`
addition (only reached when `--monitor` is combined with `--voice`/`--wakeword`, or when
`--monitor` is absent — the latter was smoke-tested, the former wasn't, since it requires a live
voice session). Low risk — it's a one-line keyword-argument addition to an already-tested
constructor call, and the constructor itself is covered by the stub-agent tier above.

## Explicitly deferred / not this session's scope

- **MQTT event subscriber → event bus → policy-gated autonomous action** — still hard-blocked on
  Faz 6 hardware (no Zigbee coordinator dongle, no Home Assistant instance). Nothing to do here
  until hardware is acquired — same conclusion as last session, unchanged.
- **A structural (not prompt-only) fix for the L2 residual risk** (finding #6) — flagged, not built.
  Candidate: a separate, read-only-only tool set for `proactive_turn()` via its own `build_graph()`/
  `get_llm(..., tools=...)` call, rather than sharing `self._graph`'s full tool set. Real added
  complexity (a second compiled graph, kept in sync with the main one on every model-switch/
  MCP-connect) — didn't feel proportionate to build blind for a feature that ships off by default;
  worth reconsidering if `monitor_proactive_enabled` ever becomes the recommended default.
- **No Electron/mobile UI for the `needs_confirmation` notification path** — same pre-existing gap
  as the interactive confirmation flow (`docs/SAFETY.md`'s Known limits); `proactive_turn()`
  deliberately routes around it (toast/push naming the gated tool, not a dangling WS event) rather
  than pretending a UI exists to complete that round-trip.
- **21 stray `.claude/worktrees/*` scratch branches** — still deferred, still needs owner go-ahead
  (destructive, unrelated to this session).

## Git state as of this session

- Branch: `langgraph-migration`, **not merged to `main`**.
- **Faz 5 was committed this session** (`eaa9c25`, 19 files, +843/-224) — see git log. This session
  started with Faz 5 fully uncommitted (per the prior session's own note); owner confirmed
  committing it first before any Faz 7 diff began, so the two phases' diffs don't mix.
- **Everything from Faz 7 is uncommitted** (per this project's standing instruction: only commit
  when explicitly asked; this session wasn't asked to commit Faz 7). `git status`: 8 modified
  source files (`jarvis/agent.py`, `jarvis/monitor.py`, `jarvis/config.py`, `jarvis/gcp_quota.py`,
  `jarvis/cli.py`, `jarvis/api.py`, `jarvis/__main__.py`, `.env.example`) plus this doc sync
  (`ROADMAP.md`, `docs/ARCHITECTURE.md`, `docs/SAFETY.md`, `MEMORY.md`, `CLAUDE.md`, `CHANGELOG.md`,
  this file). No new files this phase (unlike Faz 5's new `mcp_integration.py` — Faz 7 extended
  existing modules only).

## Recommended next steps (pick up here)

1. **Decide on commit strategy for Faz 7** — one coherent phase, same shape as Faz 4/5/6-skip; the
   owner's call, not assumed.
2. **Faz 6 — Fiziksel Dünya / IoT** stays `⛔ deferred` — hardware-gated (Zigbee coordinator dongle +
   a Home Assistant instance; owner has only an RP2040 today). Nothing to do here until hardware is
   actually acquired.
3. **If `monitor_proactive_enabled` gets turned on for real daily use**, watch `data/audit_log.jsonl`
   for `monitor-email`/`monitor-calendar`-transport entries for a while before trusting it
   unattended — the L2 residual risk (finding #6 above) is mitigated, not eliminated, and this
   feature has had exactly one local-model owner-facing trial run (this session's verification).
4. **Faz 8 — Temizlik & Konsolidasyon** is the only remaining unstarted phase that isn't
   hardware-gated (retire `jarvis/legacy/`, clean up stray worktrees, merge to `main`, add a minimal
   test suite) — a legitimate next-session candidate alongside/after Faz 6 staying blocked.
5. Confirm whether the 21 stray worktrees/branches should be cleaned up (still deferred, still needs
   owner go-ahead — destructive, unrelated to this session).

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
ollama serve                          # confirm it's up: curl http://localhost:11434/api/tags —
                                       # started fresh this session, not left running between
                                       # sessions (confirmed, same as every prior session's note)
python -m jarvis --monitor            # toast/FCM-only unless MONITOR_PROACTIVE_ENABLED=True in
                                       # .env — try that + a real new calendar event/email to watch
                                       # a live proactive suggestion end-to-end (not done this
                                       # session — only synthetic/short-lived triggers were tested)
python -m jarvis --api --monitor      # same, but the always-on path — this is where it matters
```

New `.env` vars this session: `MONITOR_PROACTIVE_ENABLED` (default `False`),
`MONITOR_PROACTIVE_MIN_GAP_SEC` (default `600`) — see `.env.example`. No new Python dependencies.
