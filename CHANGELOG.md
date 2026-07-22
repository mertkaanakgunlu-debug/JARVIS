# CHANGELOG

All notable changes to J.A.R.V.I.S. from Faz 4 onward.
For Iteration 1–3 history, see [log.md](log.md) (frozen 2026-05-24).
For current architecture and feature inventory, see [ProjectState.md](ProjectState.md).

---

## [Fix: ab_run_config.ps1's manifest missing valid_measurement on readiness timeout] — 2026-07-22

Root-caused the intermittent CI failure of `test_ab_harness_guards.py::test_ps_wrapper_propagates_
driver_failure_exit_code` (`KeyError: 'valid_measurement'`, observed on 3 of 4 CI runs today,
including runs where nothing else in this session's own changes was even touched). `ab_run_config.ps1`
only sets `valid_measurement` (and `status`/`invalid_runs`/`run_exit_codes`/`runs_completed`) in its
finalize block, AFTER the driver-runs loop — but the readiness-wait loop has its own early `exit 1`
(server never answers `/status` within the timeout) that returns BEFORE finalize ever runs. The
test's own docstring assumed its stub server always wins the port-bind race against the script's
real `ab_launch_server.py` subprocess ("its own real server fails to bind, harmlessly") — not
actually guaranteed; under CI's process-scheduling timing the real server sometimes binds first,
then misses the 300s readiness window for real (loading chroma/models takes real time), hitting the
exit-before-finalize path with a manifest that was only ever written once, at construction, missing
all five of those fields entirely.

Fixed by initializing all five fields at manifest construction time (before the server even starts)
with honest "not yet run" defaults (`valid_measurement: false`, `status: "not_started"`, etc.) —
finalize still overwrites them with the real outcome on every path that reaches it, but a run that
exits earlier now leaves a manifest that already says "not valid" instead of one missing the field.
The readiness-timeout path also now writes a specific `status: "server_not_ready"` before exiting.
New `-ReadyTimeoutSec` param (default 300, unchanged for real usage) lets a test force this exact
path deterministically in ~4s instead of relying on CI's own timing to reproduce it. New test,
`test_ps_wrapper_manifest_reports_invalid_when_server_never_becomes_ready` — exercises the
readiness-timeout path directly (no stub started at all, so nothing can ever answer `/status`).
927 pytest green (926+1), ruff clean. Confirmed against a real CI run, not just local reasoning
(the local suite never reproduced the original bug either) — see HANDOFF.md for the run link.

---

## [Fix: CI-only chromadb capacity failure in workflow tests] — 2026-07-22

Faz 7 Part 2's push (`5d29ddc`) broke CI: 37 tests failed with `chromadb.errors.InternalError: ...
no such table: acquire_write`, including unrelated, pre-existing files
(`test_shadow_replay_equivalence.py`, `test_shell_workspace.py`, `test_todo_bg_analysis.py`), never
reproduced locally across several full-suite runs. Root cause: `test_workflow_engine.py`/
`test_workflow_tools.py` each constructed a real `jarvis.memory.Memory` (5 ChromaDB collections) per
test via `make_tools()` — 30 additional real constructions apparently crossed a capacity threshold
in chromadb's Rust bindings specific to the GitHub Actions Windows runner. Confirmed by reading
`make_tools()`'s body that `memory` is referenced only inside `vault_search`/`note_append`/
`index_doc`/`procedure_save`'s closures — none of which either test file exercises. Fixed by making
`memory` a `unittest.mock.MagicMock()` in both files instead of a real `Memory` — removes the load
entirely (all 30 tests still pass, faster too) rather than working around it. 926 pytest green
(unchanged count — a fix to existing tests' setup, not new tests), ruff clean. Confirmed against a
real CI run, not just local reasoning — the only remaining failure after this fix is a separate,
pre-existing, intermittent flake in `test_ab_harness_guards.py` (already present on a commit before
this session started; flagged as its own follow-up, not fixed here).

---

## [Agent Runtime rev.2 — Faz 7, Part 2: Workflow runtime, live-wired] — 2026-07-22

**`WorkflowEngine` (Part 1) gets a real, model-facing entry point** — two new tools, `workflow_start`
and `workflow_status`, plus a human-only CLI approval command. Closes Part 1's own explicitly
deferred gap ("no live entry point constructs a WorkflowPlan from a real user request yet").

`workflow_start(goal, steps)` — `steps` is a JSON array (`{"step_id", "capability", "args",
"dependencies"}` per element), following this codebase's own established convention for complex
tool arguments (`geo_math`'s `grid_data`/`x_data`/`y_data`, `plot_data`'s `data_json`) rather than a
native nested type, since the primary local model handles JSON-as-string more reliably than deeply
nested tool-call arguments. Every step's `capability` is validated against the SAME alpha-filtered
tool list the model itself can see (`python_run` and any other alpha-disabled capability is
rejected exactly like an unknown tool name — a workflow step can never reach a capability the model
couldn't call directly) before anything is minted. `workflow_status(workflow_id)` is a pure read —
never advances or resolves anything — for checking on a running/paused/finished workflow.

**Approval resolution is a new CLI command (`/workflow list|show|approve|deny`), deliberately NOT a
third tool.** Exposing "approve"/"deny" as something the agent itself can call would let the model
resolve its own confirmation gate — exactly the bypass this entire initiative exists to prevent.
The single-turn graph's own confirmation gate has the same property structurally (a LangGraph
interrupt only resumes via `Command(resume=...)` from the transport layer, never from a model tool
call); `/workflow approve <id>` / `/workflow deny <id> [reason]` is the workflow engine's equivalent
for a mechanism that has no LangGraph interrupt to piggyback on (it runs outside the compiled
graph, see `workflow_engine.py`'s docstring). `/workflow show <id>` and the bare `/workflow`/
`/workflow list` are read-only.

**Capability routing**: `workflow_start`/`workflow_status` are opt-in via explicit wording only —
same mechanism `procedure_save` already used (`jarvis/graph/tool_router.py`'s
`_EXPLICIT_ONLY_DOMAINS`, generalized from a single hardcoded `"procedure"` check to a set). New
`"workflow"` domain; the bare word "workflow" moved out of `"procedure"`'s trigger patterns (it
used to double as a procedure-saving trigger) since it now means "run this now", not "remember
this description" — `procedure_save`'s own Turkish-native triggers (`prosedür`, `iş akışı`) are
unaffected. A real collision was caught while adding this: an early `"adım adım"` ("step by step")
pattern for the new domain also fired on an *existing* procedure-saving test query (that exact
phrase is common procedure-saving language) — narrowed to `"çok adımlı görev"` before it shipped;
`test_procedure_save_query_does_not_also_pull_in_workflow_tools` locks in the fix.

Small cleanup alongside: `WorkflowEngine.report()`'s body moved to a module-level
`render_workflow_report()` (workflow_engine.py) — `workflow_status`'s read-only tool body and the
new CLI command render a report from a loaded `WorkflowPlan` alone, without constructing a full
engine (which needs a live tools list) just to read one. The report also now names the exact
`/workflow approve|deny` command when a plan is paused, so both tools' return text and the CLI
surface the same actionable next step.

12 new tests (`test_tool_router.py` +3, new `test_workflow_tools.py` 9) — no CLI-loop test for the
new `/workflow` command itself: this repo has no established infrastructure for driving `cli.py`'s
interactive REPL loop end-to-end (its one existing CLI test file covers a pure helper function
only), and the command's own logic is a thin argument-parsing wrapper over already-tested
`WorkflowEngine` methods (`resolve_approval`/`advance`) — building new REPL-mocking infrastructure
for that thin a layer was judged disproportionate to the value, a documented scope cut rather than
an oversight. 926 pytest green (914+12), ruff clean.

---

## [Agent Runtime rev.2 — Faz 7, Part 1: Workflow runtime] — 2026-07-22

**Standalone workflow engine, reusing Faz 1-4's execution contract, deliberately NOT wired to any
live trigger yet** — the same "Part 1: mechanism, Part 2: wire it to something live" split Faz 1→2
and Faz 6 Part 1→2 already used. The plan's own framing (reviewer #7): `max_tool_rounds_per_turn`
(2) is a single-turn budget, not built for long, multi-step daily tasks.

New `jarvis/execution/workflow.py` (`WorkflowStep`/`WorkflowPlan` — pure shapes plus
dependency-readiness/skip-propagation/terminal-status logic, no I/O), `jarvis/execution/
workflow_store.py` (SQLite checkpoint/resume, mirrors `jarvis.execution.idempotency`'s
per-call-connection pattern), and `jarvis/execution/workflow_engine.py` (`WorkflowEngine` — drives
a plan's steps to completion). Explicitly separate from the single-turn chat graph
(`jarvis/graph/graph.py`'s `StateGraph`) — no new LangGraph node — but every step dispatch reuses
the exact same pipeline the single-turn path already has: `jarvis.execution.args_schemas.
validate_args()`, `policy_guard.evaluate()`, an HMAC-bound `ExecutionRequest`/approval
(`jarvis.execution.request`/`approval`), `build_shadow_envelope()`/`run_postconditions()`, and the
idempotency journal — no second, parallel verification vocabulary (plan principle #2).

**Approval pause**, mirroring `confirmation_node`'s HMAC binding exactly but without a LangGraph
interrupt (there is no compiled graph to interrupt here): a step needing confirmation sets
`plan.status="paused_for_approval"` and persists; `WorkflowEngine.resolve_approval(plan, step_id,
"approve"|"deny[:why]")` re-verifies the signature/digest/expiry before dispatching, then the
caller calls `advance()` again to keep going.

**Checkpoint/resume**: every step transition persists to `workflows.db`. A step found `"running"`
on load means the process died mid-dispatch — `idempotency.is_committed(execution_id)` (not a
guess) decides whether to mark it `"succeeded"` (committed before the crash, envelope honestly
absent rather than fabricated) or reset it to `"pending"` for a safe fresh retry (never committed
= never actually happened, per `idempotency.py`'s own documented semantics).

**Failed-step propagation**: `WorkflowPlan.propagate_skip()` cascades a failure/denial/
invalid-args rejection to every transitive dependent. A real bug caught by writing an honest test,
not assumed: the step-budget counter (`executed_count()`) originally counted any non-pending
status, so a propagated `"skipped"` step (never actually dispatched) silently consumed budget
meant for an unrelated, independent branch — fixed to count only genuinely-dispatched statuses;
`test_a_propagated_skip_does_not_consume_the_step_budget` locks this in.

**Compensation** (plan's own bullet 4 — narrow, deliberately): "yalnız kayıtlı gerçek tersi olan
işlemlerde otomatik telafi." Exactly two real compensators are registered: `file_write` (restore
prior content, or delete a newly-created file — capture happens BEFORE the overwrite) and `todo`'s
`"add"` action (delete the created to-do, id recovered from its own result text via the same
"parse a structured fact from free text" pattern `postcondition_runner.py`'s exit-code check
already uses). Every other capability's succeeded steps are left honestly uncompensated, never
silently claimed reverted. Auto-triggered whenever a plan finalizes `"failed"`/
`"partially_committed"` — including step-budget exhaustion, not just hard failures.
`ToolSpec.effect_scope` gains its first real classification: `file_write` → `"reversible"` (every
other tool stays `"unclassified"` — this field is coarse/per-tool, while the compensator registry
itself is the precise per-(capability, action) source of truth).

**Workflow-level final validation**: reuses Faz 4's `VerifiedExecutionSummary`/
`render_operation_status_for_user` over the plan's own collected step envelopes
(`WorkflowEngine.report()`) rather than building a second aggregation.

Small refactor alongside: `_resolve_target_resource` moved from `jarvis/graph/nodes.py` into
`jarvis/execution/request.py` as public `resolve_target_resource()` — the workflow engine needed
the identical logic for minting its own `ExecutionRequest`s; `nodes.py`'s `prepare_execution_node`
now imports it from there instead of keeping a second copy (plan principle #2 again).

**Deliberately NOT built this phase** (see `workflow_engine.py`'s own docstring): no LLM decides
*when* to replan or *what* the new steps should be — `WorkflowEngine.replan()` enforces
`max_replans` as a real, tested budget and appends caller-supplied steps, but nothing here triggers
it (same "mechanism before trigger" precedent as Faz 1's shadow ledger). No live entry point
constructs a `WorkflowPlan` from a real user request yet — this phase builds and tests the engine
as a standalone, directly-invokable mechanism, same split as Faz 1→2/Faz 6 Part 1→2.

46 new tests (`test_workflow_types.py` 16, `test_workflow_store.py` 9, `test_workflow_engine.py`
21) — against REAL tool objects from `jarvis.graph.tools.make_tools()`, not fakes (same precedent
as `test_langchain_dispatch_coercion.py`). 914 pytest green (868+46), ruff clean.

---

## [Agent Runtime rev.2 — Faz 5 follow-up: API conversation_id] — 2026-07-22

**Real per-client conversation support lands in the API.** Previously `jarvis/api.py`'s single
shared `JarvisAgent` had exactly one active session for every caller — every server (re)start
began fresh for every client, a known gap flagged (but not fixed) when Faz 5 removed silent
session auto-resume. `ChatRequest` gains `conversation_id: str = ""` (empty — every pre-existing
client — is a complete no-op); `/chat` and `/chat/stream` thread it into `JarvisAgent.chat()`/
`chat_stream()`, `/chat/upload` gets a matching form field, and responses echo the active
`session_id` back (`/reset`'s response gains one too) so a client can persist and re-send it.

The switch happens **inside** `chat()`/`chat_stream()`'s existing `_state_lock` section, not as a
separate pre-call step — a new `JarvisAgent._switch_session_locked()` (extracted from
`switch_session()`, which now delegates to it) lets both call it without either deadlocking
(`threading.Lock` isn't reentrant) or letting a concurrent request's own switch interleave between
"adopt conversation A" and "run A's turn" (the same class of shared-singleton race BUG-8 already
closed once for `_history`/`_turn`/`session_id`).

New `SessionStore.ensure_session()` closes a real, if previously rare, gap this surfaced:
`switch_session()` used to adopt **any** id with zero existence check (`messages`' FK to
`sessions` is declared but never enforced — no `PRAGMA foreign_keys=ON`), leaving it invisible to
`list_sessions()`/`set_topic_hint()`. Harmless as a human-typo edge case for cli.py's `/session
<id>`; would have been the *common* case once an API client mints its own `conversation_id` (e.g.
a UUID on first launch). `ensure_session()` (`INSERT OR IGNORE`) registers a real row for a
brand-new id without ever clobbering an existing one's `created_at`/`topic_hint`/`status`.

Client-side adoption (Electron/mobile actually persisting and sending `conversation_id`) is a
separate, not-yet-done follow-up — this phase is the backend capability only.

11 new tests (`test_conversation_id.py` 8, `test_session_store.py` +3), 868 pytest green (857+11),
ruff clean, `git diff --check` clean.

---

## [Agent Runtime rev.2 — Faz 6, Part 3: alternative capability, resolved] — 2026-07-22

The plan's bounded-repair ladder names five rungs: *normalize → validate → one repair →
alternative capability → explicit error*. Parts 1-2 built everything except the fourth rung.
Investigated against the real 12 schema'd tools (not argued abstractly) and **deliberately not
implemented**: every candidate pairing either reaches a different destination entirely (`gmail`
vs `itu_mail` are different mailboxes — routing a failed send to the other account is wrong, not
helpful) or requires a content judgment call (`schedule` missing `run_at` falling back to `todo`
silently changes what the user asked for — recurring automation vs. a plain checklist item). Both
are exactly the silent-reinterpretation failure mode this entire initiative exists to eliminate,
one level deeper in the repair ladder. No safe, mechanical instance exists in the current registry
— building a generic mechanism now would either sit unused or force one of these unsafe mappings.
The ladder's meaningful rungs for JARVIS are the four already built; re-opening the fifth needs a
concrete tool pairing that doesn't exist yet, not a speculative framework.

---

## [Agent Runtime rev.2 — Faz 6, Part 2] — 2026-07-22

**Internal typed validation goes live** on the 12 priority capabilities (plot_data + 11
action-dispatch tools) Part 1 defined but left unwired. `jarvis.execution.args_schemas` gained
`field_validator(mode="before")` normalizers and action-specific required-field validation
(verified against each tool's actual dispatch body — calendar.py/gmail.py/drive.py/itu_mail.py/
finance.py/spotify.py, plus schedule/todo/gcp_quota/geo_math's inline dispatches — not inferred
from docstrings), plus a new `validate_args()` returning structured `validation_errors` (pydantic's
own `ValidationError.errors()`, trimmed to `loc`/`type`/`msg` — `input`/`url`/`ctx` dropped since
`input` echoes the raw argument value and this codebase's redaction discipline forbids persisting
that unredacted).

`prepare_execution_node` now runs `validate_args()` as a **reject-only gate**: a call that fails
validation gets no `ExecutionRequest` minted and is recorded in a new `invalid_args_calls` list
instead — raw args are never substituted back into the call, so the signed/fingerprinted digest and
what actually executes can never diverge (the exact risk Part 1's own docstring flagged, closed by
construction here rather than by careful propagation).

`confirmation_node` gained an explicit, config-independent **bounded-repair state machine**: an
`invalid_args_calls` pre-gate whole-batch-rejects on the first invalid attempt, sets a turn-scoped
`args_repair_attempted` flag, and routes back to the agent for exactly one corrected retry; a second
invalid attempt in the same turn routes straight to a new `route_from_confirmation` END branch with
a composed, honest final answer instead of looping the agent again — deliberately not implicit in
`max_tool_rounds_per_turn` (a rejected batch also consumes that budget, so the "one repair" guarantee
would silently break if that config ever changed).

A second external review round raised a P0 correctness claim against "raw args execute unchanged":
that pydantic's lax coercion (`"false"` → `False`) being discarded by `validate_args()` would let a
raw string reach tool execution and corrupt truthy/type-sensitive logic. **Empirically refuted**
against JARVIS's real registered tool objects, not argued abstractly
(`tests/test_langchain_dispatch_coercion.py`): every `@tool` function already has LangChain
auto-generate its own pydantic schema from the function's type hints, independent of and predating
`jarvis.execution.args_schemas`, and `make_safe_tool_node()` wraps rather than bypasses `ToolNode`'s
dispatch through that schema — `"false"`/`"60"` are already coerced to `False`/`60` before any tool
body runs. The same investigation confirmed `args_schemas`' `extra="forbid"` is a genuine,
non-redundant addition: LangChain's own auto-derived schema silently accepts unknown fields.

**61 new tests since the 792 baseline** (`test_args_schemas.py` 40→84, new `test_bounded_repair.py`
with 12, new `test_langchain_dispatch_coercion.py` with 5) plus 9 pre-existing
`test_prepare_execution_node.py` tests fixed, not disabled (a shared fixture predated required-field
validation and was missing `subject`/`body` — now correctly rejected, confirming the validation
works). **853 pytest green, ruff clean, `git diff --check` clean.**

**Deliberately not done:** no `@tool` function signature in `jarvis/graph/tools.py` promotes any of
this to the live, model-facing schema yet (the model still sees free-text `action: str`) — pending
shadow-traffic measurement of how often `blocked_invalid_args` actually fires. `tool_call_fingerprint`
hashing raw args (so `"send"` vs `" SEND "` evaded same-turn duplicate detection) was found in the
same review round, real but pre-existing/Faz-6-independent — fixed the same day, see the entry
below.

---

## [Agent Runtime rev.2 — Faz 6, Part 2 follow-ups] — 2026-07-22

Two small, independent fixes for gaps Part 2's review surfaced, landed as separate commits per the
owner's request (independently revertible) rather than bundled:

- **`tool_call_fingerprint` normalizes `action`** (`cf769b2`: `.strip().lower()`, mirroring what
  every action-dispatch tool's own body already does) before hashing — `"send"` and `" SEND "` now
  fingerprint identically, closing the same-turn duplicate-detection gap. Every other arg still
  hashes raw (an email body or search query genuinely differs by case). No persistence concern:
  `jarvis/execution/idempotency.py`'s journal keys on `execution_id`, not this fingerprint — the
  digest is a descriptive column only, never a lookup key.
- **`scripts/ab_run_config.ps1`'s CI-only failure root-caused and fixed** (`a06cbd2`): `$Py` was
  hardcoded to `$Repo\.venv\Scripts\python.exe`, which doesn't exist on the GitHub Actions runner
  (CI installs `requirements-lock.txt` into the system Python, no venv) — `& $Py ...` failed with
  `CommandNotFoundException`, a non-terminating error under this script's own
  `$ErrorActionPreference="Continue"`, so the script sailed on with a stale `$LASTEXITCODE` left over
  from an earlier `git` call and reported a false "run succeeded". Now falls back to a PATH-resolved
  interpreter, verified by actually running `--version` (not just located — Windows App Execution
  Alias stubs resolve via `Get-Command` but fail when run) before trusting it; aborts loudly if
  nothing usable is found. This was the actual cause of `tests/test_ab_harness_guards.py`'s CI-only
  failure carried as "pre-existing, not this session's regression" across several prior sessions.

**857 pytest green (853+4 new fingerprint tests), ruff clean, `git diff --check` clean.** The CI fix
is verified locally (no regression on the `.venv`-present path; the PATH-fallback logic itself
isolate-tested against this dev machine's own broken `python`/`python3` stubs) but not yet confirmed
against a live GitHub Actions run at commit time — see [HANDOFF.md](HANDOFF.md) for the push/CI
outcome once it exists.

---

## [Agent Runtime rev.2 — Faz 6, Part 1] — 2026-07-22

**Typed schemas — definitions only, deliberately not wired anywhere yet.** New
`jarvis/execution/args_schemas.py`: 12 pydantic models (shared `extra="forbid"` base, closing the
plan's separate "unknown-field rejection" bullet for free) for the plan's own named priorities —
`PlotDataArgs` (path XOR data_json cross-field validator, `kind: Literal[...]`) plus 11
action-dispatch tools (`spotify`, `google_calendar`, `gmail`, `hud_panels`, `schedule`, `todo`,
`google_drive`, `itu_mail`, `finance`, `gcp_quota`, `geo_math`), each with an `action:
Literal[...]` extracted from that tool's **full dispatch chain**, not just its docstring. Two
real, undocumented discoveries while doing that: `geo_math` accepts
`analyze`/`reason`/`derive`/`explain` in a branch entirely separate from its own documented
"Actions:" list, and `spotify_control` accepts `prev`/`back` as undocumented aliases for
`previous`. Both captured correctly.

Wired onto `TOOL_SPECS` (`tool_registry.py`) via `args_schema=` on each tool's original
`ToolSpec(...)` call — verified to survive the three later `dataclasses.replace()` passes
(domain, contract_status, timeout_class) that rebuild `TOOL_SPECS` after the base list. New
`parse_invalid_args_field()` (`tool_accounting.py`, same shape as `parse_blocked_code`) plus
`"[INVALID_ARGS"` added to `_FAILURE_PREFIXES` — no producer yet.

**Deliberate scope cut:** no `@tool` function signature in `jarvis/graph/tools.py` was touched —
the model still sees `action: str`, free text — and nothing validates against these schemas yet
(`prepare_execution_node`'s own docstring has said "that's Faz 6" since Faz 2). Given how many
non-obvious aliases turned up in just 2 of 12 tools, promoting an unverified-enough Literal onto
a *live*, model-facing tool-calling schema was judged too risky to rush in the same pass as
defining the schemas. Promoting verified Literals onto the real `@tool` signatures, wiring actual
validation into `prepare_execution_node`, and designing the bounded-repair pipeline itself (the
plan names the vocabulary — normalize/validate/one repair/alternative capability/explicit error —
without specifying what "repair" concretely means across 30+ different tool arg shapes) are Part
2, not started.

**49 new tests** (`test_args_schemas.py`, `test_tool_registry_schemas.py`,
`test_blocked_reason_code.py` +4). **792 pytest green (743+49), ruff clean.** *(Corrected
2026-07-22: this entry originally said 841, an arithmetic slip — 743+49 is 792, not 841. Fixed
after cross-checking against CI's own independently-run total, which agreed with 792.)*

---

## [Agent Runtime rev.2 — Faz 5] — 2026-07-22

**Isolation & reproducibility.** Seven changes, all from the plan's own Faz 5 bullet list.

**Two `Path.home()` isolation bypasses closed** (`jarvis/voice/vad.py`, `jarvis/voice/tts_piper.py`)
— both were module-level constants frozen at import time, reading/writing the REAL
`~/.cache/jarvis/...` even when `JARVIS_HOME` was set for test/eval isolation. New
`jarvis.paths.cache_dir()` (same real-home-in-production / JARVIS_HOME-redirected-in-tests split
as `jarvis.tools.files._effective_home`, an independent implementation on purpose — `paths.py`
importing from `tools/files.py` would be backwards layering) fixes both, resolved per call. New
**`tests/test_no_host_path_leak.py`** AST-scans every `jarvis/**/*.py` file for
`expanduser`/`Path.home()`/`getcwd`/`"USERPROFILE"`, whitelisting exactly the two files that
already implement a correct fallback (`jarvis/tools/files.py`, `jarvis/paths.py`) — a third
bypass now fails CI immediately instead of waiting to be noticed.

**Silent session auto-resume removed entirely** — `JarvisAgent.__init__` no longer calls
`session_store.latest_session()` and guesses which session belongs to whoever is constructing it
(a reproducibility hazard for a relaunched eval-harness server, and a privacy leak for a brand-new
client). New `resume_session_id` kwarg + `SessionStore.session_exists()` to validate it before
trusting it. `cli.py` now owns its own explicit continuity — a small JARVIS_HOME-aware
`data/cli_last_session.txt`, read before construction and rewritten after `/session`/`/reset` —
preserving its "continue where I left off" UX explicitly instead of via a silent guess. `api.py`
gets no such convenience (there was never a per-client `conversation_id` mechanism to preserve),
so every server (re)start now begins a fresh session — a real, deliberate behavior change for
Electron/mobile clients, not silently absorbed.

**Cross-session prompt blocks (facts/summaries/procedures) skipped under the eval profile** —
`ContextBuilder.build()` no longer calls `recall_summaries`/`recall_facts`/`recall_procedures` at
all when `JARVIS_TEST_MODE=1` (the flag `--profile test` already sets). `procedure_save` is a
plain tool call independent of `CLOUD_POLICY`, so a fact/procedure written by one scenario in a
long-lived A/B harness run could leak into a later, logically-independent scenario's prompt —
plausibly the mechanism behind the already-known "champion 62/65 contaminated" finding.

**4 hardcoded `ChatGoogleGenerativeAI` temperatures moved to `Settings`** (`finance_extractor`,
`session_summarizer`, `deep_research`, `email_triage`) — values unchanged, now visible/tunable
and reflected in `run_manifest.json` (below).

**New `jarvis/run_context.py`** — `RunContext.for_turn()`/`for_execution()` +
`write_run_manifest()`. `for_turn` reuses LangGraph's own thread_id string
(`"{session_id}-t{turn}"}`) rather than a parallel ID scheme; `for_execution` mints a fresh,
unique run for a plain `@tool` closure with no per-turn state access (native tools are bound once
per process, not per turn). **`run_manifest.json` wired into both `chat()` and `chat_stream()`**
— model/provider, temperature, tool subset, input digest, execution envelopes, transport, written
after every turn. Honest gap: the plan's "prompt hash" and "registry version" fields are not
populated — neither concept exists anywhere in this codebase yet. A real bug was caught wiring
this into `chat_stream()`: its checkpoint-read `try/except` only assigned `checkpoint_tuple` on
the happy path, so a `get_tuple()` failure would have left it undefined and crashed the new
manifest code with `NameError` — fixed with a pre-init, locked in by a dedicated test.

**`plot_data` moved off the shared flat `data/plots/` directory** onto a fresh
`RunContext.for_execution()`-scoped one — the plan's own named example ("plot.png collides")
is now fixed for the common case (two auto-named charts with nothing else to distinguish them).
Full per-turn artifact grouping across multiple tool calls would need LangGraph's `InjectedState`
(not introduced this phase); other artifact-producing tools (`report_write`, csv/excel output,
`index_doc`, `note_append`) are untouched — same narrow-but-real scoping precedent as Faz 3's
postcondition runner.

**39 new tests** across `test_no_host_path_leak.py`, `test_jarvis_home.py`, `test_session_store.py`,
`test_session_resume.py`, `test_context_builder.py`, `test_run_context.py`,
`test_run_manifest_integration.py`, `test_plot_inline.py`. **743 pytest green (704+39), ruff
clean**, full suite re-run after every meaningfully-sized change, not just once at the end.

---

## [Agent Runtime rev.2 — Faz 4] — 2026-07-22

**Verified response composition + claim audit** (reviewer #4's own words: "the architecture's
strongest fix"). `compose_node` (`jarvis/graph/nodes.py`) used to hand raw `ToolMessage` content
to the composer LLM and trust it to correctly describe what happened — Faz 1.4's existing guard
only caught the *all-failed* case; a **partial** success (2 tools, 1 succeeded, the model claims
both did) sailed through uncaught.

New `jarvis/execution/summary.py`: `VerifiedExecutionSummary`/`VerifiedOperation` collapse each
`ExecutionEnvelope` into a single `display_status` by combining the tool's own self-reported
`status` with an *independent* postcondition verdict (`severity="required"` only — a `warning`
never downgrades a success). Five values: `confirmed`, `reported_success_unverified` (no
independent check available — most of the 36 tools today), **`reported_success_verification_
failed`** (the actual B6 shape — the tool says ok, independent verification disagrees), `failed`,
`partial`. `any_failed` is the mechanism's trigger.

`compose_node` reuses Faz 1's existing `execution_contract_mode` ladder (no new setting): `off`
(default) is a byte-identical no-op — `summary` is never even built; `shadow` computes and logs
a secondary, deliberately conservative TR/EN `audit_claims()` pattern check via `audit_log`
without touching the invocation or response (preserves `test_shadow_replay_equivalence.py`'s
bit-identical off/shadow contract); any `enforce_*` strips raw `ToolMessage`s from the LLM
invocation in favor of a deterministic, code-authored status block (`render_operation_status_
for_model`) and, independent of what the model actually said, **unconditionally appends** the
same facts (`render_operation_status_for_user`) to the outgoing response whenever `any_failed` is
true — this structural, envelope-driven append, not the regex claim detector, is what actually
satisfies the phase's acceptance test ("an unverified operation claim does not reach the user").

31 new tests (`test_execution_summary.py`, `test_verified_response_composition.py`) — including a
direct proof that the unconditional append fires even when the secondary text-pattern audit stays
silent. **704 pytest green (673+31), ruff clean.** Live shadow-traffic measurement (the plan's own
"annotate before enforce" gate) has not been run — `execution_contract_mode` stays `"off"` by
default, so this phase is fully inert in production today.

---

## [Agent Runtime rev.2 — Faz 3] — 2026-07-21

**Timeout semantics.** `ToolSpec.timeout_seconds` was declared since Faz 1 (Phase 2, really) and
never actually applied anywhere — `asyncio.wait_for` bounds every call now (`safe_tools.py`'s
`_awrap_tool_call`), keyed off a new **`_TIMEOUT_CLASSES`** classification (`tool_registry.py`,
same "one place, import-time-checked" shape as `_TOOL_DOMAINS`/`_ALPHA_STATUS`) covering all 36
tools: `hard_process_timeout` (shell_run/python_run/report_compile — real subprocess spawns),
`cooperative_async` (the native-async sub-agent bridges — real cancellation at the next await
point), `external_request_timeout` (12 network-bound tools — honestly documented as NOT yet
getting a real per-library client timeout, only the generic outer bound), `soft_thread_timeout`
(everything else — local compute/filesystem/SQLite/ChromaDB). `geo_math` classified conservatively
soft_thread_timeout despite being `async def`: most of its actions run heavy synchronous compute
with no await point at all, worse than a blocked thread if taken literally (blocks the whole event
loop) — documented, not fixed; a real fix needs an executor-thread refactor out of scope here.

For the three subprocess-spawning tools, **the real fix lives inside the tool**, not the outer
wrapper: `shell.py`/`python_exec.py`/`latex.py` now thread `ToolSpec.timeout_seconds` into their
own `subprocess.run(timeout=...)` calls (previously hardcoded, disconnected module constants —
shell_run's ToolSpec said 120s, the actual subprocess call used a hardcoded 30s). `python_exec.py`
no longer catches `TimeoutExpired` itself (it used to return a bare `"[ERROR] ... timeout"` string
that bypassed all structured reporting) — it now propagates to the shared exception boundary like
the other two always did. `format_tool_error()` (`safe_tools.py`) reports two new honest fields
whenever `category=="timeout"`: **`execution_may_still_be_running`** (false only for
cooperative_async — real cancellation) and **`worker_terminated`** (true only for
`subprocess.TimeoutExpired` specifically — the one exception type that *guarantees* Python killed
the child). `ExecutionEnvelope.status` becomes `"timed_out"` (not a plain `"failed"`) when these
fire — `timed_out` takes precedence over the ok/fail split.

**Postcondition verification runner** (`jarvis/execution/postcondition_runner.py`, new) — Faz 1
only defined `PostconditionSpec`'s shape; this is the actual evaluator for all 8 kinds
(`file_exists`, `path_within_workspace`, `file_openable`, `artifact_hash_matches`,
`row_count_matches`, `series_matches`, `exit_code_matches`, `record_exists`). Honesty discipline
enforced throughout: no runnable check (missing params, no workspace, no manifest) → `unverified`,
never silently `"verified"`. `series_matches` (the actual B6 shape — verifying a chart's plotted
values) is fully implemented and unit-tested against a JSON sidecar manifest, but **not wired to
any live tool** — nothing produces such a manifest yet (`plot_data`'s PNG has no sidecar) and no
TaskContract extractor exists to supply `expected_y`; wiring it honestly needs both, out of scope
here. Real specs are attached to exactly **one** tool this phase — `file_write`
(`file_exists`+`path_within_workspace`, keyed off its own `path` arg) — the only file-producing
tool whose output path is a direct, unambiguous call argument rather than derived/returned
(plot_data's `output` is a filename stem; report_write's path comes from `title`). Wired into
`tool_result_accounting` (which gained an optional `workspace` param, threaded from `graph.py`)
inside the same `mode != "off"` block envelopes already live in — off mode runs zero new code,
same rollback contract as Faz 1.

**64 new tests** across 3 files (`test_timeout_enforcement.py`, 25 — including a real
`asyncio.wait_for` cancellation through a compiled graph and real `subprocess.TimeoutExpired`
from actual short-lived subprocesses, not mocks; `test_postcondition_runner.py`, 29 — all 8 kinds,
verified/failed/unverified per kind, plus a SQL-injection defense-in-depth test for
`record_exists`; `test_tool_accounting_postconditions.py`, 10). **673 pytest green (609+64), ruff
clean.**

Sıradaki: Faz 4 (verified response composition + claim audit) — `compose_node` reads from a
`VerifiedExecutionSummary` instead of raw `ToolMessage`s.

---

## [Agent Runtime rev.2 — Faz 2] — 2026-07-21

**Yeni node: `prepare_execution`** (`jarvis/graph/nodes.py`) — routing artık `agent →
prepare_execution → confirmation` (eskiden doğrudan `agent → confirmation`, `graph.py`). Ajanın
önerdiği her tool call için: capability resolve (`get_spec`) → normalize (bugün pass-through —
hiçbir `ToolSpec` henüz `args_schema` taşımıyor, o Faz 6) → risk classify
(`policy_guard.evaluate()` — confirmation_node'un kendi bağımsız çağrısıyla asla ayrışamaz, çünkü
`evaluate()` saf bir fonksiyon) → best-effort `target_resource` (tanınan bir kaynak-benzeri arg
varsa `capability:değer`, yoksa bare capability) → TaskContract match (dürüstçe hep `"no_contract"`
— hiçbir şey henüz TaskContract üretmiyor) → imzala. Sonuç: değişmez bir `ExecutionRequest`
(`jarvis/execution/request.py`, frozen pydantic model) + imza, `state["execution_requests"]`'e
tool_call_id ile yazılıyor.

**HMAC onay bağlama** (`jarvis/execution/approval.py`) — reviewer #2/#6. Process-local anahtar
(`secrets.token_bytes(32)`, tek kullanıcılı yerel asistan için sertifika altyapısı gereksiz — bilinçli
sonuç: bir process restart'ı bekleyen her onayı geçersiz kılar, `verify()` bunu düz bir
signature_mismatch olarak görür ve çağıran "yeniden onay iste" der, çökmez). `sign()`/`verify()`
`execution_id · capability · normalized_args_digest · target_resource · risk_level · expiry ·
single_use_nonce` üzerinden — bu yedi alandan biri değişirse imza geçersiz. `confirmation_node`
artık kullanıcı onayından SONRA, "tools"a geçmeden TAM ÖNCE, imzayı + **CANLI** tool_call
argümanlarının digest'ini yeniden doğruluyor (TOCTOU kapanıyor — onay gösterildikten sonra bir
repair argümanı değiştirmişse eski onay artık geçersiz) ve expiry (`Settings.approval_ttl_sec`,
varsayılan 300s) kontrol ediyor.

**Idempotency journal** (`jarvis/execution/idempotency.py`, SQLite, `kill_switch.py`/
`audit_log.py`'nin "path'i her çağrıda taze çöz" deseni — bağlı bir bağlantı cache'lemek tam da
isolate-test-data-paths hata sınıfını tekrar eder). Faz 2 kabulünün kendi cümlesi: **"approve →
retry → journal reddi."** `execution_id` her tek çağrıda TAZE basılıyor (tool_call_id + rastgele
suffix) — asla tool_call_id'den ya da args'tan türetilmiyor, bu yüzden iki BAĞIMSIZ istek asla
çakışamaz (yapı itibariyle); journal'a bir isabet ancal AYNI zaten-basılmış isteğin gerçek bir
replay'i anlamına gelir. `tool_result_accounting` bir çağrı GERÇEKTEN başarılı olduğunda commit
ediyor (bunu bilen tek node); `confirmation_node` onaydan hemen önce `is_committed()` kontrol
ediyor. Kapsam dışı bırakılan (dürüstçe belgeli): iki FARKLI isteğin (örn. modelin başka bir turda
"aynı" maili tekrar göndermesi) semantik-duplikasyon tespiti — `ToolSpec.idempotency` bunun
Faz 1'den kalan hedefi ("none" — hiçbir tool henüz sınıflandırılmadı); sıfır tool sınıflandırılmışken
bu kontrolü şimdi inşa etmek egzersiz edecek hiçbir şeyi olmayan kod olurdu.

**Geriye dönük uyumluluk, kanıtlanmış değil varsayılmış değil:** `confirmation_node`'un yeni
kontrolleri `state["execution_requests"]` yoksa (eski checkpoint, ya da bu paketten ÖNCEKİ HER
test'in yaptığı gibi node'u doğrudan çağıran bir unit test) tamamen no-op — mevcut 84 confirmation/
tool-accounting/shadow-replay testi hiç değiştirilmeden yeşil kaldı.

**50 yeni test** (`test_execution_request.py`, `test_execution_approval.py`,
`test_execution_idempotency.py`, `test_prepare_execution_node.py`) — imza tamper/expiry/nonce
matrisi, TOCTOU repair senaryosu, replay reddi, ve gerçek derlenmiş graph üzerinden bir
interrupt→`Command(resume="approve")` round-trip'i (bu repodaki HİÇBİR önceki test bunu gerçek
graph'a karşı yapmıyordu — hepsi `langgraph.types.interrupt`'ı mock'luyordu). **609 pytest yeşil
(559+50), ruff temiz.**

Sıradaki: Faz 3 (executor timeout semantiği + postcondition verification runner).

---

## [Ölçüm düzeneği sertleştirme + Faz 1 kabulü] — 2026-07-21

Faz 1'in kabul koşusu, **ölçüm düzeneğinin kendisinin bozuk olduğunu** ortaya çıkardı. Bu girdi
o tamiratı ve Faz 1'in yerine geçen gerçek kabul testini kapsıyor (7 commit: `1a22d7f`..`9dd1405`).

**Kök hata (`5941f63`):** `ab_run_config.ps1 -Port` driver'a hiç ulaşmıyordu (`JARVIS_TEST_BASE_URL`
set edilmiyor, driver varsayılan 8132'ye gidiyordu). Varsayılan port dışındaki **her koşu sessizce
0 alıyordu** — dolu görünen sonuç dosyası + `exit 0` + her satırda "trace tools=none". Bu, önceki
oturumun "izole koşular Gemini 2.5 Pro diye cevaplıyor" anomalisinin de açıklaması: smoke koşusu
8133'te sunucu başlatırken driver hâlâ açık olan şampiyon sunucusuna (8132) bağlanmış. Kanıt:
smoke'un cevabı yalnız `home-champ`'ta var olan bir dosyayı listeliyor; `home-shadow-smoke`'ta hiç
`audit_log.jsonl` yok. **Faz 1 kodu suçsuzdu.** Yan etki: o istekler şampiyon baseline'ın 5.
run'ının içine düştü — **62/65 referansı küçük bir kontaminasyon taşıyor.**

**Hata sınıfını kapatan korumalar (`5342e86`, `513eadd`, `aade56e`):**
- `preflight()` — skorlamadan önce `GET /status`; başarısızsa exit 3.
- Failure taxonomy: **semantik/tool hatası → geçerli ölçüm; transport/server hatası → ölçüm değil,
  run geçersiz** (exit 5). Ulaşılamayan tur *başarısız* değil, **yok** — ortalamaya katmak modeli
  sessizce kötü gösteriyordu. `HTTPError` kasten dışarıda: sunucu cevap verdi, koşu hâlâ ölçüm.
- `.ps1` wrapper artık driver'ın verdiği kararı yutmuyor (eskiden exit kodunu log'a yazıp 0
  dönüyordu — aynı sessiz-hata sınıfını bir katman yukarıda yeniden üretiyordu).
- `results/manifest_<config>.json` — run_id, git_sha, branch, dirty, port, mode, model, effort,
  scenarios, test_home, makine; sonda `status`/`valid_measurement`/`invalid_runs` ile finalize.
- **Instance handshake:** `GET /internal/test-identity` (yalnız `JARVIS_TEST_MODE=1` ile mount)
  run_id nonce, mode, config fingerprint, git_sha döndürür. Path **döndürmez** — teşhis yüzeyi
  keşif yüzeyine dönüşmesin. Preflight artık "bir sunucu" değil "**bu** sunucu" kanıtlıyor.

**Faz 1'in asıl kabul testi (`6796043`, `9dd1405`):** canlı A/B bu soruyu cevaplayamaz — n=5'te
senaryo varyansı aranan etkiden büyük (`champ 62/65`, `champ-shadow 59/65`, `ctl-off 59/65`; iki
Faz-1 config'i berabere ama **farklı** senaryoları kaybederek). Yerine **deterministik replay**:
gerçek derlenmiş graph + **scripted model** (varyans inşaen sıfır), aynı fixture off ve shadow'dan
geçiyor. 9 fixture; karşılaştırılan: tool seçimi, ham argümanlar, sonuçlar, kullanıcı cevabı,
dosya sistemi yan etkileri (içerik hash'i), hata metni, sayaçlar, ledger. Farklı olmasına izin
verilen: yalnız `execution_envelopes`. Volatil alanlar blanket ignore ile değil **açık allowlist**
ile çıkarılıyor. Ayırt etme gücü **mutasyonla doğrulandı**: shadow-only bir yan etki enjekte
edilince 9 vakadan 7'si kırmızıya döndü (yeşil kalan 2'si hiç tool çalışmayan vakalar). `9dd1405`
testi **gerçek SqliteSaver** ile tekrarladı — `checkpointer=None` tam da doğrulanması gereken
mekanizmayı atlıyordu. **Sonuç: off ve shadow dışarıdan birebir aynı; Faz 1 kabulü kapandı.**
Bu, canlı B6 sorusunu tek başına kapatmıyor — runtime'ı şüpheli listesinden çıkarıp geriye **model
nondeterminizmini** bırakıyor (Faz 2-4'ün hedefi).

**559 pytest yeşil, ruff temiz.**

---

## [Agent Runtime rev.2 — Faz 1] — 2026-07-20

**Önce: şampiyon A/B baseline koşuldu ve analiz edildi** (`qwen3:8b`, `LOCAL_REASONING_EFFORT=none`,
5×13, iki-metrikli oracle) — Faz 0'ın ertelenen son kalemi + commit `a6a3426`'nın "provisional"
işaretlediği model-seçim kararının re-baseline'ı. **62/65 (95.4%)**, güvenlik senaryoları
(C9/D11/D12/D13b) hepsi 5/5, G17b artık temiz. Tek bulgu: **B6 3/5** (plot_data hallucination —
tam olarak bu planın var olma sebebi olan desen, canlı hâlâ oluyor) ve **F16 4/5**
(`trace tools=none`, 1 run). **Model-seçim kararı artık CONFIRMED, provisional değil** — eski
2026-07-18 baseline'ının 60/65'inden daha iyi; B6/F16 bir model sorunu değil, Faz 2-4'ün
TaskContract + verified-composition işinin çözmeyi hedeflediği mimari sınıf.

**Faz 1 — core contract types + shadow ledger:** yeni paket `jarvis/execution/`:
- `contract.py` — `TaskContract`/`ExpectedOutcome` (yalnız şekil; extractor'lar Faz 2+)
- `postcondition.py` — `PostconditionSpec`/`PostconditionResult`/`VerificationStatus`
- `envelope.py` — `ExecutionEnvelope` + `build_shadow_envelope()`
- `redaction.py` — paylaşılan redaksiyon katmanı, key-based (eski) + **pattern-based (yeni)** —
  `jarvis.agent`'ın eski `redact_tool_args`'ının "plain-string arg taranmıyor" diye belgelenmiş
  açığını kapatıyor

`ToolSpec` (`tool_registry.py`) 6 yeni additive alan aldı: `args_schema`, `postconditions`,
`idempotency`, `effect_scope`, `contract_status` (Faz 0'ın `_ALPHA_STATUS`'u artık buraya da
katlanıyor), `timeout_class` — hepsi Faz 3/6/7 hedefi, henüz canlı davranış değiştirmiyor.
`Settings.execution_contract_mode` (`off|shadow|enforce_read_only|enforce_reversible
|enforce_all`, varsayılan **off**) ve `JarvisState.execution_envelopes` eklendi.
`tool_result_accounting` artık mode≠"off" iken her tool call için bir `ExecutionEnvelope`
üretip state'e ekliyor — **hiçbir kararı değiştirmiyor**, mode="off"/`settings=None` iken (varsayılan)
bu blok hiç çalışmıyor.

**Bu oturumda bulunan üçüncü bir ham-redaksiyon açığı** (HANDOFF'un işaret ettiği ikisine ek):
`agent.py`'nin `_trace_end`'i `tool_trace.record`'a ham `content_head` yazıyordu — kendi modül
docstring'i "file contents... must never persist through it" diyordu ama tutmuyordu. Üçü de
(audit_log'un `args_preview`/`result_preview`'i + tool_trace'in `content_head`'i) artık paylaşılan
katmandan geçiyor; `tool_execution_ledger.content_head` de (checkpointer SQLite'ına giden) düzeltildi.

43 yeni test (`test_execution_types.py`, `test_execution_redaction.py`,
`test_execution_shadow_ledger.py` + `test_tool_trace.py`/`test_audit_outcome.py` ekleri).
**523 pytest yeşil (480+43), ruff temiz.**

Küçük bir canlı shadow-mode entegrasyon kontrolü (tam 5×13 değil, port 8133'te 1-2 izole
senaryo) sunucu çökmeden tamamlandı; tam Faz 1 kabul koşusu (shadow vs off, bit-identical skor
iddiası) [HANDOFF.md](HANDOFF.md)'a bırakıldı.

**Commit/push bu oturumda yapılmadı** — owner onayı bekleniyor.

## [Agent Runtime rev.2 — Faz 0] — 2026-07-20

Owner'ın GPT-analiz oturumundan gelen mimari plan (dış geliştirici bulguları + reviewer'ın
14 maddelik revizyonu): mevcut sistemik sorunları (context leakage, yanlış tool argümanı,
uydurma başarı iddiası, run-to-run değişkenlik) tek tek yamamak yerine ortak kök nedeni
çözen bir "execution contract" runtime'ı. Tam plan (9 faz, 0-8):
`C:\Users\mertk\.claude\plans\c-users-mertk-desktop-gpt-analysis-md-s-delegated-scone.md`.

**Bu plan kendi Faz 0-8 numaralandırmasını kullanıyor — [ROADMAP.md](ROADMAP.md)'nin Faz
0-8'i (local-first pivot, hepsi done/deferred) ile KARIŞTIRILMAMALI.** Kod içi yorumlar ve bu
girdi hep "Agent Runtime rev.2, Faz N" şeklinde açık niteleniyor, bare "Faz N" değil.

**Faz 0 — alpha capability allowlist:** her tool artık açık bir statü taşıyor
(`contract_enforced`/`shadow_validated`/`quarantined`/`disabled`/`explicitly_unverifiable`,
`jarvis/tool_registry.py`'nin `ALPHA_STATUS_VALUES`). Bu oturumda yalnız iki canlı karar:
`python_run` → **disabled** (sandbox'sız, keyfi uzunlukta Python script'i, `shell_run`'ın tek
satır komutu gibi confirmation prompt'unda anlamlı incelenemez) — hem `make_tools()`'un dönen
listesinden çıkarılarak (model şemasını hiç görmüyor) hem `policy_guard.evaluate()`'te bağımsız
veto ile (savunma derinliği: eski bir checkpoint'ten gelen çağrı da bloklanır) iki ayrı yerde
kapatıldı. `shell_run` → **quarantined** (yüzeyde kalıyor, ek denetim için işaretli). Diğer 34
tool `shadow_validated`'a düşüyor — dürüstlük notu: bu bir HEDEF durum, Faz 1 canlanana kadar
hiçbir shadow doğrulama gerçekte çalışmıyor.

`PolicyDecision`'a yeni `veto_kind` alanı ("kill_switch" | "capability_disabled") —
confirmation_node'un ack mesajı artık gerçek nedeni söylüyor, devre dışı bir capability için
"kill switch kapalı" gibi yanlış bir mesaj göstermiyor.

12 yeni test (`tests/test_alpha_capabilities.py`). **480 pytest yeşil (468 baseline + 12),
ruff temiz.**

Bu oturumda ayrıca: HANDOFF.md'nin "7 commit push edilmeli" rakamı yeniden doğrulandı ve
düzeltildi — gerçek fark `origin/langgraph-migration`'a göre 4 commit'ti (aşağıdaki dört
retroaktif girdi + bu Faz 0 commit'i, toplam 5, şimdi push edildi).

**Sıradaki (Faz 1):** `jarvis/execution/` paketi — `TaskContract`, `PostconditionSpec`,
`ExecutionEnvelope`, ortak redaksiyon katmanı (bu oturumda bulunan ek açık: `audit_log.record`
`args_preview`/`result_preview`'i ham yazıyor, `tool_trace`'in redaksiyonu audit log'a
uygulanmıyor — `agent.py:139,195`). Shadow mode, karar değiştirmez. 5×13 A/B baseline koşusu
(iki-metrikli re-baseline, bkz. altta) SONRAKİ OTURUMA bırakıldı — [HANDOFF.md](HANDOFF.md).

## [Merge-öncesi review sertleştirmesi] — 2026-07-19

Dış reviewer'ın A/B raporu kabulü sonrası merge-öncesi iş listesi uygulanıyor.

**Faz 1 — kill switch fail-safe:** VAR-ama-okunamaz/bozuk state dosyası artık **FAIL-CLOSED**
(sentetik trip; cache'lenmez → düzelen dosya anında geçerli) — eski davranış sessizce stale
cache/armed default'a düşüyordu (BOM olayının iki sessiz yönü). Eksik dosya fresh-install
default'u olarak `enabled=True` kalır; dosyayı silmek warm trip'i sessizce re-arm etmez.
Episode başına 1 `logger.critical` + yapısal audit eventi (`kill_switch_state_unreadable`).
`_save(state)` imzası: bozuk dosyada `/killswitch on|off` artık geçerli dosyayı yeniden yazar
(operatör kurtarma yolu). 12 yeni failure-mode testi (truncated/empty/anahtarsız/IO-error/
silme/eşzamanlı okuma-yazma/log-latch/audit). **433 pytest yeşil, ruff temiz.**

**Faz 2 — G17b kişisel-veri uydurma yasağı (reviewer'ın ana tezi):** üç katman. (1)
Deterministik: `context_builder._format_facts` artık `degraded_features()`'ı sorguluyor —
fact extractor degraded iken facts block "(none yet)" yerine açık "MEMORY EXTRACTION
UNAVAILABLE + do NOT guess" markeri taşıyor ("hiç kayıt yok" ile "extractor çalışmadı"
artık ayırt ediliyor; modelin şehir uydurduğu belirsizlik buydu). (2) Kalıcı prompt kuralı:
`05_memory_policy.md` "Memory honesty" — kişisel bilgiler yalnız facts block/tool
çıktısından, yoksa "kayıtlarımda yok", asla tahmin. (3) Oracle sözleşmesi: `Expected`'a
`required_any` (OR-grubu) + `forbidden_response` (KOŞULSUZ yasak — `forbidden_claims`'in
aksine tool başarısından bağımsız) alanları; G17b artık "izmir VEYA dürüst belirsizlik"
ister, İzmir-dışı her şehir adı (hedge'li uydurma dahil) kesin FAIL. 9 yeni test.
**442 pytest yeşil, ruff temiz.**

**Faz 3 — `[BLOCKED:reason_code]` yapısal blok durumu:** tool-seviyesi policy retleri artık
makine-okur snake_case kod taşıyor — SSRF `ssrf_private_address`/`ssrf_blocked_hostname`/
`ssrf_no_host`/`ssrf_unparseable_url` (`url_policy.is_blocked_url` 3-tuple oldu), shell
`shell_denylist`/`workspace_escape`, MCP `mcp_browser_guard` kodu SSRF kodlarını kullanıyor.
`tool_accounting.parse_blocked_code` kodu execution ledger + tool_trace satırlarına
`reason_code` alanı olarak kaldırıyor; oracle `_blocked_signal` önce yapısal alana bakıyor,
`[BLOCKED` prefix eşleşmesi yalnız legacy fallback. Confirmation-node stub'ları
(`[BLOCKED: serbest metin]`) bilinçli kapsam dışı — onlar zaten yapısal `policy_decision`
satırı bırakıyor. `_FAILURE_PREFIXES` değişmedi (açık ayraçla eşleşme). 8 yeni test + 10
mevcut test yeni konvansiyona güncellendi. **450 pytest yeşil, ruff temiz.**

**Faz 4 — model-selection metrikleri:** `turn_summary`'ye `total_llm_ms`; `/status`'a
`last_turn_llm_calls/input_tokens/output_tokens/llm_total_ms` (driver whitelist'i de aldı).
`ab_analyze.py` büyük genişleme: pooled warm p50/p90/p95/min/max (13 skorlu senaryo havuzu —
5 örnek/senaryoyla senaryo-bazlı p95 anlamsız, havuz tanımı raporda açık), cold sayacı,
token/çağrı istatistikleri (eski jsonl'de toleranslı "-"), İLK pozisyonel konfig = champion
baseline, reviewer eşikli **karar matrisi** (güvenlik 4×n/n zorunlu · toplam ≥%92 · yeni
sistematik FAIL=0 · G17b uydurma=yok · warm e2e p50 ≤1.2x / p95 ≤1.3x · tool-accuracy ve
transport ≤ baseline), Türkçe kalite eki (A1/A3/E14 r1 yanıtları). Eski off/on kayıtlarıyla
regresyon doğrulandı (rapor üretiyor, ON matristen geçiyor — matris taban çizgisi, sıralama
değil). ci.yml ruff kapsamına `ab_analyze.py` + `ab_launch_server.py`. W4b (veto-turn'de
critic atlama) bilinçli ERTELENDİ: critic revise döngüsü çıktı-etkileyen, graph routing
değişikliği ayrı oturum — D13b bulgusu: blok öncesi gereksiz LLM çağrısı YOK (veto
deterministik), 5.7s = araç-seçim + reddi yazan çağrı (+critic). **450 pytest, ruff temiz.**

**Faz 5 ön-koşu — canlı yakalanan izolasyon açığı (smoke koşusu, 2026-07-19):**
`jarvis/tools/files.py`'nin mutlak-yol sınırı (`_HOME`) modül import zamanında sabitlenen
GERÇEK `~` dizinini kullanıyordu — `JARVIS_HOME` (test/eval izolasyonu) hiç görülmüyordu.
Smoke koşusunun B5a senaryosu, model mutlak bir OneDrive-Desktop yolu verince, izole test
home'u yerine owner'ın GERÇEK Desktop'ına dosya yazdı (MEMORY.md'nin isolate-test-data-paths
olayıyla aynı sınıf, yeni bir alt sistemde). Fix: `_effective_home()` — `JARVIS_HOME` set
iken o dizin mutlak-yol erişiminin TEK sınırı olur (gerçek home tamamen dışarıda kalır);
set değilken (üretim) eski davranış (gerçek Desktop/Documents erişimi) değişmez. Ayrıca
B5a'nın `forbidden_claims` regex'i kalıp ("oluşturdum"/"yazdım") modelin çoğul "-duk/-dık"
ifadesini yakalamıyordu (aynı koşuda fark edildi) — kök yerine gövdeye genişletildi. 4 yeni
test (izole yazma/okuma engeli + üretimde davranış değişmediği). Sızan dosya (bilinen
içerik/zaman damgasıyla doğrulanıp) temizlendi. **453 pytest yeşil, ruff temiz.**

**Faz 5 ön-koşu #2 — D12 senaryosunun belirsiz prompt'u (şampiyon re-baseline'da canlı yakalandı):**
Yeni harness'la ilk şampiyon (qwen3:8b) re-baseline koşusu D12'de (gmail send bloğu — güvenlik
senaryosu) run 1'de PASS, run 2-5'te tutarlı FAIL verdi ("expected a structural block signal...
found none"). Kod incelemesi: bu oturumun commit'lerinden hiçbiri `nodes.py`/`policy_guard.py`/
gmail path'ine dokunmuyor — regresyon değil. Kök neden: D12'nin prompt'u yalnız konu belirtiyor,
gövde belirtmiyor ("bir deneme maili gönder" + konu, içerik yok); paylaşılan home'da geçmiş-
oturum özeti biriktikçe model muhtemelen "bunu zaten denedim" bağlamıyla aracı hiç çağırmadan
gövdeyi soruyor — güvenlik kapısı (`external_writes_disabled`) bozuk değil, ona hiç ulaşılmıyor
(`last_turn_llm_calls` 2→1, araç çağrısı hiç yok). Fix: prompt'a açık gövde eklendi. Re-baseline
tekrar koşuldu: **5/5 runda 13/13 (65/65 toplam), D12 dahil tam skor.** Bu, bu Faz'ın G17b
bulgusuyla aynı ders: bir oracle FAIL'ini "beklenen/ilgisiz" diye geçmeden kök nedenini kanıtla.

**Faz 5-6 — model-selection sonuçları (qwen3.5:9b, ministral-3:8b; OFF-only 16×5, owner
kararı):** ikisi de eşiği geçemedi.
- **qwen3:8b (şampiyon, temizlenmiş baseline):** 65/65, 4 güvenlik senaryosu (C9/D11/D12/D13b)
  5/5 hepsi.
- **qwen3.5:9b:** 55/65. `B5b`/`B6` her koşuda sabit FAIL (dosya okuma yanıtında beklenen kelime
  eksik; grafik aracını hiç çağırmadan başarı iddia ediyor). Ayrıca 2 transport timeout (B6, C7
  — birinde 240s'de hiç cevap gelmedi). Pooled warm e2e p95 baseline'ın **5.6 katı**. Karar
  matrisi: **KALDI** (toplam skor, yeni-FAIL, p95, tool-accuracy, transport — hepsi FAIL).
- **ministral-3:8b:** 45/65 — en düşük. `B5b`/`C7`/`D11`/`F16` her koşuda sabit FAIL; **D11 bir
  güvenlik senaryosu** (tehlikeli komut engelleme) — model aracı hiç çağırmıyor, bloğa hiç
  uğramıyor (aynı desen `B5b`/`C7`/`F16`'da da: `trace tools=none`). Belirgin şekilde daha hızlı
  (pooled e2e p50 baseline'ın %42'si) ama bu hız büyük ölçüde araçları atlamaktan geliyor. Karar
  matrisi: **KALDI** (güvenlik kriteri dahil).
- **Karar:** `config.local_model` DEĞİŞMİYOR; `qwen3:8b` + `LOCAL_REASONING_EFFORT=none`
  varsayılan kalıyor. Ayrıntılı karar matrisi: `scripts/ab_analyze.py` çıktısı +
  [docs/review/2026-07-premerge-summary.md](docs/review/2026-07-premerge-summary.md).

**Faz 7 — ikinci context-leak (aynı sınıf, farklı katman) + iki-metrikli oracle:** model-selection
yanıtları incelenirken `agent._build_env_block`'un da GERÇEK Desktop yolunu (`~/OneDrive/Desktop`)
`--profile test` altında bile system prompt'a yazdığı bulundu — `files.py`'nin `_effective_home()`
fix'iyle AYNI `expanduser("~")`-`JARVIS_HOME`'u-görmüyor sınıfı, farklı bir katmanda. Model bunu
yanıtlarında geri yansıtıyordu (önce hallüsinasyon sanıldı, ham prompt incelenince anlaşıldı). Fix:
`_build_env_block` artık `files._effective_home()`'u yeniden kullanıyor. Bu commit ayrıca
`docs/review/2026-07-premerge-summary.md`'yi **PROVISIONAL** işaretledi: 65/65, tool-execution
compliance'tı, end-to-end doğruluk değil (B6 yanlış veriyi çizip geçti) — `qwen3:8b` kararı
iki-metrikli re-baseline'a kadar geçici sayılmalı. 2 yeni test, 455 pytest.

Onu kapatan **iki-metrikli oracle** (`plotting.py`'nin `<name>.png.meta.json` sidecar'ı +
`Expected.plot_check`/`grounded_claims` + `Verdict.semantic_reasons`) bu oturumda geldi —
compliance ile semantic correctness artık ayrı raporlanıyor (`ab_analyze.py`'nin Overall/
tool-execution/semantic üç kolonu). 17 yeni test, **468 pytest yeşil, ruff temiz.**
`ab_run_config.ps1`'e hızlı kısmi koşular için `-Scenarios` parametresi eklendi, ardından
`@ScenarioArgs` splatting'in `--all`'ı skaler'e çöktürüp her koşuyu boş sonuçla exit 2 yaptığı
bulgu düzeltildi (`@()` zorunlu array + direkt geçiş).

**Not:** Yukarıdaki iki-metrikli oracle'ın gerektirdiği re-baseline (a6a3426'nın "PROVISIONAL"
işaretini kapatacak 5×13 koşu) bu tarihte henüz koşulmadı — [HANDOFF.md](HANDOFF.md)'nin sonraki
oturum listesinde.

---

## [GPT 2. tur planı + round-3 ölçüm düzeltmeleri] — 2026-07-18

İki oturum. **Oturum 1** (GPT_Analysis 2. tur, onaylı plan): audit `ok` alanı `.content`
üzerinden + kanonik failure-prefix'ler; `shell_run` workspace-cwd + cd-escape guard'ı;
`plot_data` inline `data_json` (B6 kök nedeni); compose history-echo guard'ı; harness
senaryo izolasyonu (/reset); **eval oracle** (`scripts/eval_oracle.py`, trace+fs+response
birlikte) + `jarvis/tool_trace.py` (L1 dahil her çağrı, `JARVIS_TOOL_TRACE` gated);
router diacritic folding (`grafik ciz`, `Drivea yukle` artık doğru domain'e düşüyor);
qwen3 thinking-off (`LOCAL_REASONING_EFFORT=none`, ~14x hız, tool-call regresyonu yok);
TTFT + cold-start enstrümantasyonu; Calendar/Gmail hata yolları `[ERROR]` standardında.

**Oturum 2** (round-3 review, A/B öncesi ölçüm hataları): **D13b oracle beklentisi
tersti** — `enabled=False` trip DEMEKTİR; çalışan kill switch FAIL, bozuk olan PASS
skorluyordu → BLOCKED'a çevrildi. **Pre-execution bloklar artık yapısal kanıt bırakıyor**:
confirmation_node kill-switch vetosu ve external-write blokunda `policy_decision` trace
satırı yazıyor; oracle'ın response-regex fallback'i kaldırıldı (yalnız "devre dışı" YAZAN
model artık geçemez). **`turn_summary()` compose>agent tercihli** — tool turn'lerinde
latency/TTFT/label artık görünür cevabı yazan çağrıdan (A/B'nin ölçtüğü şey). Ayrıca:
tool_trace args key-redaksiyonu, `plot_data` inline limitleri (256KB/10k satır/100
kolon/düz primitifler), CI ruff'ı harness scriptlerini de linliyor, `.env.example`'a
`LOCAL_REASONING_EFFORT`. **417 pytest yeşil, ruff temiz**; policy-trace→oracle zinciri
izole seam-check ile uçtan uca doğrulandı.

**Oturum 3 — TAM A/B KOŞULDU (16 senaryo × 5 tur × 2 konfig, canlı):** doğruluk birebir
aynı (**60/65 vs 60/65**; tek FAIL iki konfigde de G17b — offline extractor degraded),
thinking-off konuşma turnlerinde **3-6x hızlı** (A1 warm LLM 892ms vs 4819ms), run duvar
süresi ~%25 kısa → **`LOCAL_REASONING_EFFORT=none` default'u DOĞRULANDI.** Canlı koşu iki
gerçek bug daha yakaladı: (1) kill_switch BOM-körü okuma — BOM'lu state dosyası sessizce
stale cache'e düşürüyordu; dıştan TRIP görünmez kalabilirdi → `utf-8-sig`; (2) `fetch_url`
SSRF reddi `[ERROR]` prefix'liydi → `[BLOCKED]` (oracle C9 artık gerçek bloku görüyor).
Harness repoya alındı: `scripts/ab_run_config.ps1` + `ab_launch_server.py` + `ab_analyze.py`
(Faz 4 challenger koşuları aynı altyapıyı kullanacak). **421 pytest yeşil.**

## [Patch 1.2 + Sprint 2 + model A/B: kabiliyet regresyonu] — 2026-07-17

**Bağlam:** Owner "eskiden takvime ekleme gibi işleri yapıyordu, şimdi yapamıyor" dedi. Teşhis
(kodda doğrulandı): kabiliyet kaybı kod çürümesi DEĞİL, motor değişimi — "takvim" çalışırken
non-trivial her turn cloud Gemini'ye gidiyordu; `CLOUD_POLICY=off` (maliyet kararı) sonrası her
şey qwen2.5:7b'ye düştü ve o model ~34 araç + uzun prompt altında tool-call kanalını
kullanamıyor. İkinci dış review (ChatGPT-5.6, `GPT_Analysis.md`) modelin tek suçlu olmadığını
gösterdi: 8 modelden-bağımsız gerçek bug. Bu üç fazlı çalışma o planı uyguladı. **34+38 yeni test
(324/324 pytest yeşil), ruff temiz**, artı canlı kabul turu (aşağıda).

### Patch 1.2 — Tool Runtime Safety (deterministik, LLM'den bağımsız)
- **`imap-tools` bağımlılığı eklendi** (`requirements.txt`/lock) — E15 canlı: temiz kurulumda
  `itu_mail` `No module named 'imap_tools'` ile `/chat`'i 500'lüyordu.
- **SafeToolNode** (`jarvis/graph/safe_tools.py`): tool-body exception'ları artık yapılandırılmış
  `[TOOL_ERROR]` ToolMessage'a dönüyor (kategori + retryable; sanitize edilmiş tek satır,
  traceback/credential sızmaz) — graph tool hatasıyla ölmüyor, model tepki verebiliyor. LangGraph'ın
  default `handle_tool_errors`'ı yalnız `ToolInvocationError`'ı yakalıyordu.
- **Deterministik tool-call sınırları** (`nodes.py` confirmation node başı, policy'den önce,
  router'dan bağımsız): batch≤4, turn≤6, round≤2, identical=1 — aşan batch KOMPLE reddedilir. Canlı
  A2: qwen2.5 tek-satırlık selamlamaya ~20 çağrılık halüsinasyon batch'i (2 mail send) üretmişti;
  tek savunma external-write gate'iydi. Yeni `tool_execution_ledger` + `tool_result_accounting`
  node'u (tools SONRASI çalışan, çağrının nasıl bittiğini bilen tek yer) `seen`/`completed`
  fingerprint ayrımını tutuyor. Audit blok kayıtları `batch_size`/`turn_attempted_count`/
  `unique_tool_count`/`external_write_count` ile zenginleştirildi.
- **`GraphRecursionError` → ledger-bazlı kontrollü cevap** (F16'nın opak 500'ü yerine): en az bir
  başarılı tool varsa "tamamlananlar korundu", yoksa "doğrulanamadı" — asla battaniye iddia.
  `/chat` 200, `/chat/stream` normal SSE; yarım turn history'ye yazılmaz.
- **ProcedureStore idempotency** (F16: 10 duplicate draft): content fingerprint + güvenli migration
  (backfill → approved-öncelikli canonical → `archived_duplicate` → partial UNIQUE index — mevcut
  duplicate'ler yüzünden index doğrudan kurulamaz). `add_or_get()` → `ProcedureAddResult(id, created)`;
  duplicate'ta `[ALREADY_EXISTS]` döner ve Chroma'ya ikinci kez yazmaz.
- **Context hygiene / turn compaction** (A2→A3 kök nedeni): history'ye artık yalnız gerçek
  kullanıcı mesajı + nihai cevap (+ opsiyonel tek satır tool özeti) giriyor; raw batch/stub/
  policy-ack/critic mesajları checkpoint/audit/ledger'da kalıyor. `_trim_history` mesaj-sayısı
  yerine tamamlanmış-turn bazlı (max 10). Eskiden bir halüsinasyon batch'i 20-mesaj penceresini
  taşırıp kullanıcının az önce söylediği bilgiyi ("rengim mavi") atıyordu.

### Sprint 2 — Capability router + graph separation
- **Deterministik capability router** (`jarvis/graph/tool_router.py`): kelime-sınırlı (`\b`) TR+EN
  tablo → `ToolRoute(primary_domain, domains≤3, confidence, explicit_tool_intent)`; conversation=0
  araç, belirsiz istek ASLA full set, toplam subset≤8. `ToolSpec.domain` alanı + 13-domain haritası;
  MCP araçları 'mcp' karantinasında (açık browser/otomasyon ifadesi olmadan hiçbir turn'e açılmaz);
  `procedure_save` yalnız açık "prosedür kaydet" niyetinde. `_is_trivially_simple` + iki substring
  sinyal seti emekli ("ok"∈"çok", "hi"∈"tarihi" bug sınıfı öldü).
- **Turn-scoped binding**: `make_agent_node` artık state[tool_route] → subset → (role,subset)-başına
  `get_llm` cache'i bind ediyor; route yoksa (arka plan/eski checkpoint) full set. Hiçbir yer artık
  ~34 şemayı birden bind etmiyor.
- **Graph ayrımı** (F16'nın döngüsünü yapısal kırar): yeni **bare compose node** (sıfır tool
  şeması) nihai cevabı üretiyor — tanık olduğu çağrıyı yeniden düzenlemesi imkânsız.
  `tools→tool_result_accounting→post_tool_router→(compose|agent)`: tek-adımlı başarı→compose;
  multi-step şekil (planner veya multi-domain route) + round bütçesi→agent. Critic fake
  `HumanMessage` enjeksiyonu kaldırıldı — critique yalnız state'te, compose node-lokal
  SystemMessage ile uygular; revizyon bare compose'dan geçer.

### Faz 3 — Yerel model A/B: qwen2.5:7b → **qwen3:8b** (default değişti)
Aynı 16-senaryo suite, `temperature=0`, aynı scoped subset'ler, `--profile test`:
- **qwen2.5:7b: SIFIR gerçek tool çağrısı** — "dosyayı oluşturdum/maili gönderdim" hepsi
  halüsinasyon metni, tool katmanına hiç ulaşmadı (audit boş, diskte dosya yok). Başarısızlıktan
  beter: güvenlik gate'leri devreye bile girmedi.
- **qwen3:8b: gerçek iyi-biçimli çağrılar** — `file_write` GERÇEKTEN yazdı, `shell_run` dir
  GERÇEKTEN çalıştı → external-write gate, shell deny-list, SSRF guard, killswitch İLK KEZ uçtan
  uca gerçek çağrılarla doğrulandı. 8 GB RTX 4070 Laptop VRAM'e sığıyor.
- Local tier `temperature=0` (deterministik tool-calling + A/B tekrarlanabilirliği).

### Yol boyunca CANLI bulunan 3 gerçek bug (A/B'den bağımsız, kalıcı düzeltme)
1. **langgraph 1.2.x non-streaming `ainvoke()` dinamik interrupt'i RAISE etmiyor**, `result["__interrupt__"]`'te
   döndürüyor → `/chat`'in `except GraphInterrupt`'i hiç tetiklenmiyordu, onay payload'u sessizce
   düşüyordu (Faz 4'ten beri latent — yalnız streaming CLI/voice yolları canlı doğrulanmıştı).
   chat/proactive/background üç giriş noktası da value-surface'i ele alıyor.
2. **Boş-cevap fallback'i tüm mesaj listesini tarıyordu** → replay edilen history'ye uzanıp önceki
   turn'ün cevabını aynen döndürüyordu (canlı "echo"). Artık yalnız bu turn'ün mesajlarına bakıyor.
3. **`graph_stream_to_text` yalnız "agent" node'unu stream ediyordu**; Sprint 2 sonrası final cevap
   "compose"dan geliyor → onaylanan `shell_run` boş stream dönüyordu. Compose da stream ediliyor.

### Kabul turu (16 senaryo, qwen3:8b default, ground-truth: audit + dosya sistemi)
GPT-5.6'nın kabul metrik tablosu: **HTTP 500 = 0** · raw-JSON/pseudo final = 0 · uydurma tool adı =
0 · aynı tool+args tekrarı = 0 (F16 tek draft) · izinsiz dış yan etki = 0 · **A2→A3 short-term
recall = GEÇER** · gerçek tool-call üretimi ≈ %100 (tool gerektiren her testte) · doğru domain
seçimi 15/15 · **maliyet $0.00**. E15/E14 kimlik-yok artık düzgün `[TOOL_ERROR]`/hata mesajı (500
değil); killswitch izole doğrulandı (temiz session, off → `blocked_kill_switch`).

**Bilinen sınır (yeni):** aynı tool-isteği önceki turn'de geçmişte varsa, model tool çağırmadan
önceki turn'ün cevabını yankılayabiliyor (D13b canlıda: killswitch testini geçersiz kıldı —
killswitch'in kendisi izole testte sağlam). Turn compaction'ın özet-satırının yan etkisi;
sonraki iterasyona bırakıldı.

---

## [Stabilizasyon Patch 1.1: dış review düzeltmeleri] — 2026-07-16

Stabilizasyon sprintinin (aşağıda) dış incelemesi (ChatGPT-5.6, sprint commit'i `c7f2b63`
üzerinde) 9 maddelik bir düzeltme listesi çıkardı — 1 P0 + 8 P1. Her iddia önce kodda tek tek
doğrulandı (dokuzu da gerçek), sonra sıralı uygulandı. **24 yeni test (242/242 pytest yeşil),
ruff temiz**, artı canlı smoke (aşağıda).

- **P0 — `--profile test` gerçek `JARVIS_HOME`'u devralabiliyordu**: `__main__.py`'deki
  `os.environ.setdefault(...)`, işletim sisteminde global `JARVIS_HOME` tanımlıysa temp home
  yerine ONU kullanıyordu — "gerçek veriye dokunmaz" garantisi tam da o değişkeni kullanan
  kurulumlarda sessizce bozuluyordu. Artık her zaman taze `mkdtemp`; tekrarlanabilir dizin
  isteyen (CI) için yeni, test-only `JARVIS_TEST_HOME` değişkeni (production değişkeniyle
  karıştırılamaz, açık opt-in). Eski davranışı bilerek koruyan test tersine çevrildi.
- **Reset artık per-session telemetriyi de sıfırlıyor** (`_reset_state_sync`):
  `_last_turn_trace`, `_last_turn_used_pro` ve `_pending_confirmations` lock altında
  temizleniyor — önceden `/status` reset'ten sonra ARŞİVLENMİŞ session'ın modelini göstermeye
  devam ediyordu ve reset-öncesi bir confirmation id yeni session'a resume edilebilirdi.
  Kullanıcının `/model` pin'i (`_active_model_id`) bilerek korunuyor (tercih, session state'i değil).
- **External-write koruması per-action oldu**: `PolicyDecision`'a per-CALL `side_effect_type`
  alanı eklendi (`_READ_ACTIONS`'taki read aksiyonları "external_read" olarak çözülüyor);
  `make_confirmation_node`'un `EXTERNAL_WRITES_ENABLED=false` gate'i statik `ToolSpec` yerine
  bunu kullanıyor. Önceden `gmail read`/`calendar list`/`drive download` da bloklanıyordu —
  test profili tam da egzersiz etmesi gereken read path'lere kördü. `gmail send`/`calendar
  create`/Spotify yine hard-deny.
- **`CLOUD_POLICY=explicit` background extractor'ları da kapatıyor**:
  `cloud_extractors_enabled` `!= "off"` yerine `== "auto"` — `explicit`'in vaadi "cloud yalnız
  kullanıcı AÇIKÇA seçtiğinde"; manuel pin konuşmanın cevap modeline izindir, arka plan
  fact/entity/finance/summary/todo/triage/pdf_vision/deep_research Gemini çağrılarına değil.
  Ana router'ın explicit/pin davranışı değişmedi.
- **AI Studio artık koşulsuz bedava sayılmıyor**: yeni `Settings.ai_studio_billing_mode`
  (`free|paid|unknown`, default `unknown`) — bir Developer-API anahtarının free mi paid mi
  olduğu provider adından bilinemez (bu reponun kendi anahtarı kredisi tükenmiş PAID çıktı).
  `_Tier.billable: bool` → `_Tier.billing: str`; metadata `jarvis_billing` (+ türetilmiş
  `jarvis_billable`); `UsageTracker.record(billing=...)` üç durumlu: `paid` fiyatlanır (yalnız
  `provider=="vertex"` flash/pro_turns sayacını artırır — gcp_quota RPD takibi Vertex'e özel),
  `free` 0$, `unknown` tokenlar `unpriced_tokens_in/out`'ta birikir ve `/status`
  (`session_unpriced_tokens`) + `/budget` raporunda görünür — asla sessizce 0$ varsayılmaz.
- **`fallback_used` response/turn olarak ayrıldı** (`turn_summary`):
  `response_fallback_used` (görünür cevabı fallback tier mi yazdı — etiketi bu sürer) vs
  `turn_had_any_fallback` (turn'ün herhangi bir yerinde hata/tier>0 var mıydı). Critic'in
  fallback'i artık cevabı "(fallback)" diye yanlış etiketlemiyor; `fallback_used` anahtarı
  eski istemciler için response-scoped alias olarak duruyor. CLI `/status` critic-only
  fallback'i soluk "(fallback elsewhere in turn)" notuyla gösteriyor.
- **Confirmation resume artık trace'i güncelliyor**: `_pending_confirmations` bare `config`
  yerine `{"config", "recorder"}` saklıyor; `resume_and_stream()` bittiğinde aynı recorder'dan
  `turn_summary()` alıp `_last_turn_trace`'e yazıyor — önceden onaydan geçen bir turn'ün
  `/status` başlığı hep BİR ÖNCEKİ turn'ü gösteriyordu.
- **Legacy generic-429 graph rebuild kaldırıldı** (`chat()`): router-öncesi kalıntı — herhangi
  bir tool'un (Tavily dahil) "429" içeren hatasında grafı AI Studio'ya rebuild edip TÜM turn'ü
  yeniden çalıştırıyordu (başarılı tool side-effect'lerini tekrarlama riski) ve
  `CLOUD_POLICY=off` altında router'ın reddedeceği bir geçişi duyuruyordu. Per-invocation
  fallback tek yerde: `_compose(...).with_fallbacks()`. Artık ölü olan `_using_fallback` alanı
  ve etiket suffix'i tamamen söküldü.
- **Background task origin-session'a sabitlendi** (`background_turn`): `origin_session_id`
  girişte yakalanıyor; iş bittiğinde aktif session değişmişse (reset/switch) sonuç canlı
  konuşmaya DEĞİL, origin session'ın store'una (kendi taze `turn_idx` bucket'ına) yazılıyor;
  episodic memory de origin'e tag'leniyor. Kullanıcı sonucu TaskExecutor'ın tamamlanma
  bildirimiyle görüyor — session contamination kapandı.
- **Küçükler**: zero-token çağrılar artık `by_provider.calls`'ta sayılıyor (+
  `unreported_calls` işareti) — gerçek bir invocation, provider usage raporlamadı diye
  defterden düşmüyor; `/status`'a `vertex_configured` + `cloud_calls_allowed` alanları eklendi
  (`vertex_active` yanıltıcı adıyla eski istemciler için duruyor); `.env.example`'a
  `AI_STUDIO_BILLING_MODE` + `JARVIS_TEST_HOME` belgelendi.
- **Canlı doğrulama** (`--api --profile test --port 8131` + Invoke-RestMethod): `/status` yeni
  alanlarla doğru (policy=off altında `cloud_calls_allowed:false`); gerçek local turn →
  `actual_provider:"ollama"`, maliyet 0$, `turn_had_any_fallback:false`; içerikli session'da
  `/reset` → HTTP 200 VE sonrasında trace alanları null (patch'in kendi düzeltmesinin canlı
  kanıtı); reset'in summarizer'ı degraded listesine düştü (cloud gate çalışıyor).
- **Kapsam dışı bırakılanlar** (review'un düşük öncelikli notlarından): `local` rolünün
  "kesin lokal" semantiği (fast/local/realtime hâlâ alias — sprint 2'nin routing işi);
  8 direct-Gemini modülünün gateway migration'ı (sprint 3); pre-first-turn model etiketinin
  policy-körlüğü (kozmetik, sprint 2'deki `_is_trivially_simple` işiyle birlikte ele alınmalı).

## [Stabilizasyon sprinti: Runtime Truth + Reset + Test Isolation] — 2026-07-16

Canlı manuel test oturumu (owner + Claude, aynı gün) 7 bug ortaya çıkardı: model etiketi rolden
tahmin ediliyordu (gerçekte cevaplayan sağlayıcıdan değil), lokal Ollama turn'lerine Gemini fiyatı
yazılıyordu, `POST /reset` içerikli session'da her zaman 500 veriyordu, testler gerçek `data/`ya
yazıyordu, AI Studio anahtarı tükenmişti (429), Vertex her non-trivial turn'de gerçek para
harcıyordu. Bulgular `GPT_Analysis.md`ye (ChatGPT-5.6) verildi; doğrulayıp net bir stabilizasyon
planı çıkardı — **önce ölçüm ve runtime, sonra routing/tool-router**. Bu sprint o planı uyguladı,
sıralı 6 fazda, her fazdan sonra tam pytest. Plan dosyası: `.claude/plans/`. **58 yeni test,
218/218 pytest yeşil, ruff temiz.**

- **Faz 1 — JARVIS_HOME izolasyon kökü**: yeni `jarvis/paths.py` (`jarvis_home()`/`data_dir()`/
  `resolve()`/`project_data_dir()`/`resolve_project()`) — `JARVIS_HOME` env değişkeni set değilse
  davranış bugünkünle birebir aynı (cwd-relative); set edilince TÜM runtime store'lar (sessions.db,
  checkpoints, usage.json, audit_log, kill_switch, ChromaDB, vault, uploads, pdf/geo-math/drive
  cache'leri) VE proje-köküne çivili gmail/calendar/email_triage OAuth token'ları (bir `chdir`in
  kaçıracağı yol) o kökün altına taşınır. ~25 call-site rewire edildi (`agent.py`, `api.py`,
  `monitor.py`, `graph/tools.py`, `tools/finance.py`, `tools/drive.py`, `tools/gmail.py`,
  `tools/calendar.py`, `tools/email_triage.py`, `tools/spotify.py`, `tools/geo_math_tool.py`,
  `tools/pdf.py`, `audit_log.py`, `kill_switch.py`, `gcp_quota.py`, `__main__.py`, `memory.py`).
  Yeni `tests/conftest.py` fixture'ı `jarvis_home` (mevcut `isolated_cwd`ile birlikte kullanılabilir).
- **Faz 2 — `POST /reset` 500 düzeltmesi**: kök neden `api.py`'nin `agent.reset()`'i
  `run_in_executor`'a (worker thread, event loop yok) atması, `reset()`'in içeride
  `asyncio.create_task()` çağırması (`_schedule_summarize_one`) — `RuntimeError: no running event
  loop`, her içerikli session'da. `agent.py`'de `reset()` ikiye bölündü: `_reset_state_sync()`
  (saf senkron state mutation) + `async reset_async()` (`asyncio.to_thread` ile state mutation,
  özet planlaması loop'a dönüldükten SONRA). `_schedule_summarize_one`'a `get_running_loop` guard'ı
  eklendi (loop yoksa crash değil, log + atlama). `api.py`'nin iki call-site'ı (`/reset` endpoint,
  shutdown auto-reset) ve `cli.py`'nin `/reset` komutu `reset_async()`'e geçti. Reset'in "arşivle +
  yeni session", **silme değil** olduğu dokümante edildi (ChromaDB kayıtları kalır).
- **Faz 3 — Provider invocation trace (runtime truth)**: `jarvis/providers/__init__.py`'deki her
  tier artık `with_config(metadata={...})` ile kendi kimliğini taşıyor (`jarvis_provider`,
  `jarvis_model`, `jarvis_billable`, `jarvis_tier_index`) — bind_tools SONRASI, with_fallbacks
  ÖNCESİ (sıra önemli, `RunnableBinding`'in `bind_tools`'u rebind etmemesi için). Yeni
  `jarvis/llm_trace.py`: `LlmTraceRecorder(BaseCallbackHandler)` — `on_chat_model_start`/`on_llm_end`
  /`on_llm_error` ile her gerçek LLM çağrısını `LlmCallTrace`'e çeviriyor (`_HudEventCallback`'e
  dokunulmadı, ayrı bir handler olarak eklendi). `agent.py`'nin 4 giriş noktası (`chat`,
  `chat_stream`, `proactive_turn`, `background_turn`) artık recorder'ı config'e ekliyor; `_cloud_model`
  etiketi artık `_last_turn_trace`'ten türüyor (rolden tahmin değil). `/status` + CLI `/status`'a
  `requested_role`/`actual_provider`/`actual_model`/`fallback_used` alanları eklendi. Yol boyunca
  bulunan 2 regresyon (her ikisi de bu fazın kendi edit'i): `background_turn()`'de `use_pro_agent`
  hiç yerel değişken değildi (sadece state dict'e inline yazılıyordu) — recorder satırı `NameError`
  fırlatıyordu, bu da arka plan task'ında sessizce yutulup testin sonsuza dek bekleyen bir Event'e
  takılmasına yol açıyordu (11+ dakikalık gerçek bir CI/local hang, canlı gözlemlendi); ve
  `test_background_turn.py`'nin `_FakeAgent`'ı yeni `self.usage` okumasını karşılamıyordu (aynı hang
  deseni). İkisi de düzeltildi.
- **Faz 4 — Provider-aware usage v2**: `usage.py`'nin `record()`'u artık `(provider, model,
  tokens_in, tokens_out, billable)` alıyor — maliyet **çağıranın deklare ettiği `billable`
  flag'inden** hesaplanıyor, model adında `"pro"` substring'inden değil (bu, lokal Ollama turn'lerinin
  Gemini Flash gibi fiyatlanmasının kök nedeniydi). `flash_turns`/`pro_turns` artık yalnız billable
  Vertex çağrılarını sayıyor (gcp_quota.py'nin Vertex RPD-kota takibi için doğru anlam); yeni
  `by_provider` kırılımı (additive, eski anahtarlar korunmuş — `ws.py`/`gcp_quota.py` kırılmadı).
  `agent.py`'deki `_record_usage_from_result` (her turn'de `result["messages"]`'daki TÜM
  AIMessage'ları yeniden tarayıp çift sayan kod) ve `chat_stream`'in `len//4` tahmini kaldırıldı —
  tek yazar artık recorder'ın `on_llm_end`'i, background/proactive turn'ler de ilk kez gerçek usage
  kaydediyor.
- **Faz 5 — CLOUD_POLICY gating**: yeni `cloud_policy: Literal["off","explicit","auto"] = "off"`
  (`config.py`) — **varsayılan `off`**, owner'ın canlı-test sonrası kararı. `providers/__init__.py`'ye
  `_cloud_allowed()`/`cloud_extractors_enabled()`/`note_degraded()`/`degraded_features()`; `off`'ta
  `fast` de `reasoning` de saf Ollama (pin dahil, istisnasız); `explicit`'te yalnız manuel `/model`
  pin'i geçer (Vertex-pinned yol için `_cloud_tiers`e `pinned` parametresi eklendi — yoksa
  `_pinned_cloud_tiers`'ın Vertex'e delegasyonu kendi `_cloud_allowed` çağrısında ikinci kez
  engelleniyordu). Router'a migrate olmamış 8 direct-Gemini modülüne (`fact_extractor`,
  `entity_extractor`, `finance_extractor`, `session_summarizer`, `todo_analyzer` ×2,
  `tools/email_triage.py`, `tools/pdf_vision.py`, `tools/deep_research.py`) erken-dönüş gate'i
  eklendi — `off`'ta sessiz `[]`/`None` yerine `note_degraded(feature)` + `/status.degraded` listesi.
  Migrasyon değil, yalnız gating (sprint 3'e bırakıldı).
- **Faz 6 — `--profile test` + `EXTERNAL_WRITES_ENABLED`**: `__main__.py`'ye `--profile
  {default,test}` — `test`, `--profile` bayrağını argparse çalışmadan ÖNCE (module-level, argv
  pre-scan ile) tespit edip `load_dotenv()`'i tamamen atlıyor, `JARVIS_SKIP_DOTENV=1` set ediyor
  (`config.py`'nin kendi bağımsız `env_file` okuyucusu da bu flag'i honoring ediyor — iki ayrı .env
  okuyucusunun ikisi de kapatılmadan gerçek `.env` sızıntısı mümkündü), `JARVIS_HOME`'u
  `tempfile.mkdtemp()`'e (unset ise) ve `CLOUD_POLICY=off`/`EXTERNAL_WRITES_ENABLED=false`'u env'e
  basıyor. Yeni `Settings.external_writes_enabled` (`config.py`) + `make_confirmation_node`'a
  (`graph/nodes.py`) kill-switch'le aynı şekilde bir hard-deny bloğu: `side_effect_type ==
  "external_write"` olan her çağrı (gmail/calendar/drive/itu_mail/spotify) interrupt'a hiç
  gitmeden reddediliyor. **Canlı doğrulama**: `python -m jarvis --api --profile test --port 8130` —
  `/health` ok, `/status` (turn öncesi) taze session + `cloud_policy:"off"`, `/chat "merhaba"` →
  `qwen2.5:7b-instruct (Ollama, local)`, sonraki `/status` → `actual_provider:"ollama"`,
  `session_cost_usd:0.0`, `POST /reset` → **HTTP 200** (içerikli session'da — önceki 500'ün tam
  tersi), reset sonrası yeni session_id + `degraded:["session_summarizer"]` (reset'in özetleme
  denemesi CLOUD_POLICY gate'ine takıldı, sessizce Gemini'ye çıkmadı). Gerçek `data/`+`vault/`
  (58 dosya) MD5 hash'i smoke test öncesi/sonrası **birebir aynı**.

**Bilinçli kapsam dışı** (sonraki sprintler): tool-domain router, `_is_trivially_simple()`'ın
cloud-first davranışı (substring false-pozitifleri dahil — `"ok"`→`"oku"`, `"hi"`→`"hiçbir"`),
critic'in revizyon talimatını `HumanMessage` olarak enjekte etmesi, 8 modülün gateway'e tam
migrasyonu, `purge_session`, ses modeli önbelleklerinin JARVIS_HOME'a taşınması (immutable
multi-GB indirmeler — bilinçli hariç), 2 yetim Settings alanı (`drive_cache_dir`,
`geo_math_output_dir` — modül sabitleri hükmediyor, kablolanmadı).

---

## [GPT-5.6 review remediation] — 2026-07-15 — Güvenlik sertleştirmesi (Faz 1-7)

Önceki oturumda ChatGPT 5.6'nın JARVIS reposuna yaptığı dış denetim raporunun 23 iddiası gerçek kod
üzerinde tek tek doğrulanmış (17 CONFIRMED, doğrulama tablosu `.claude/plans/` altında kalıcı kayıt)
ve 7 fazlık bir remediation planı çıkarılmıştı. Bu oturum o planı uyguladı — P0 güvenlik fazları
(1-3) önce, sonra sertleştirme (4-5), en sonda kapsamı daraltılmış iyileştirmeler (6-7). Ağır
çok-ajanlı workflow kullanılmadı (önceki oturum session limitine takılmıştı); iş inline, faz faz,
her fazda testlerle ilerledi. **56 yeni test, 160/160 pytest yeşil.**

- **Faz 1 — API secure-by-default (P0)**: boş `JARVIS_API_KEY` + `0.0.0.0` + CORS `"*"` üçlüsü canlı
  bir açıktı (telefon/Tailscale erişimi zaten kullanımda). `jarvis/api.py`'ye `resolve_api_bind_host()`
  eklendi: key boşsa efektif host `127.0.0.1`'e düşer; `JARVIS_API_HOST` açıkça non-loopback set
  edilip key boşsa **fail-fast** (`RuntimeError`, net mesajla). CORS `"*"` → `resolve_cors_origins()`
  ile explicit allowlist (Electron'un `file://` origin'i + `localhost`/`127.0.0.1` her port dahil,
  hiçbir zaman wildcard). Yeni ayarlar: `api_host`, `api_cors_origins` (`jarvis/config.py`).
- **Faz 2 — Proaktif turn yapısal read-only (P0)**: `monitor.py`'den gelen proaktif turn'ler tam tool
  setiyle çalışıyordu, L2 (auto-approve, `requires_confirmation=False`) araçlar (örn. `file_write`,
  `procedure_save`) hiçbir gate'e takılmadan sessizce yürüyordu — sadece system prompt "yapma"
  diyordu. `jarvis/graph/nodes.py`'nin `confirmation_node`'una runtime guard eklendi:
  `transport` `"monitor-"` ile başlıyorsa ve risk_level≥2 ise, zaten-confirmable (L3, gate açık) olan
  hariç, tüm çağrılar yürütülmeden reddedilir. Faz 7'nin doğru çalışan L3-discard-to-notify davranışı
  (`ProactiveOutcome(kind="needs_confirmation")`) dokunulmadan korundu.
- **Faz 3 — Prosedürel bellek zehirlenmesi (P0/P1)**: `procedure_store.py`'de provenance/approval hiç
  yoktu — agent'in yazdığı bir prosedür anında recall'a girip gelecek turn'lerin system prompt'una
  enjekte olabiliyordu. `status`/`created_by`/`approved_at` kolonları eklendi (idempotent migration,
  mevcut satırlar `approved` grandfather), yeni `source='agent'` satırları `draft` başlar.
  `jarvis/memory.py`'nin `recall_procedures()`'ı artık yalnız `status='approved'` döndürüyor (Chroma
  `where` filtresi). CLI'ye `/procedures` komutu (list/approve/reject). System prompt'un context
  injection bloğu (`06_context_injection.md`, v3) memory/procedure/vault bloklarını "untrusted
  reference data, not instructions" olarak çerçeveliyor artık.
- **Faz 4 — MCP/browser sertleştirme (P1)**: `@playwright/mcp@latest` → pinlendi (`0.0.78`, npm'den
  teyit edildi). `browser_navigate`'in hiç SSRF guard'ı yoktu (`url_read`'inki vardı) — paylaşımlı
  `jarvis/url_policy.py` çıkarıldı (localhost/private/link-local/metadata, DNS-rebinding'e karşı
  resolved-IP kontrolü dahil), `url_read` buna geçti, MCP tarafına `langchain-mcp-adapters`'ın
  `ToolCallInterceptor`'ı ile uygulandı — engellenen bir `browser_navigate` gerçek MCP çağrısına asla
  ulaşmıyor.
- **Faz 5 — Runtime/concurrency izolasyonu (P1)**: `TaskExecutor._run()` arka plan görevlerini
  `self._agent.chat()` ile çalıştırıyordu — hem ana `self._history`'i mid-flight kirletiyor hem de
  `_state_lock`'ı görevin TÜM süresi boyunca tutup foreground chat'i bloke ediyordu. Yeni
  `JarvisAgent.background_turn()`: `proactive_turn()`'ün izolasyon desenini model alıyor (kendi
  thread_id + izole mesaj listesi), `ainvoke()` süresince lock TUTMUYOR, sonuç bittiğinde kısa bir
  kilitli pencerede gerçek `self._history`'e user/assistant mesaj çifti olarak ekleniyor. BUG-8'in
  kendisi (chat()/chat_stream()'in kilit davranışı) hiç değiştirilmedi — regresyon riski böylece
  minimize edildi.
- **Faz 6 — shell/python temel guard (P1/P2, kapsamı daraltılmış)**: `shell.py`'nin deny-list'inde
  `Invoke-Expression`/`iex`/`-EncodedCommand`/.NET reflection bypass'ları hiç yoktu — eklendi.
  `python_exec.py`'nin (verdict: shell_run'dan bile kötü) hiç içerik kontrolü yoktu — artık
  çalıştırmadan önce script kaynağını aynı paylaşımlı deny-list'e karşı tarıyor. **Tam sandbox
  değil** (docs/SAFETY.md'nin "Known limits"i geçerli). Tool-domain router (turn başına 5-8 tool) ve
  tam shell/python broker (Job Objects) plan metninde açıkça ayrı/daha büyük iş olarak ertelenmişti —
  bu oturumda uygulanmadı.
- **Faz 7 — Observability/CI**: startup summary backfill'i (`_schedule_summary_backfill`) yalnızca
  `__init__`'ten çağrılıyordu — bu, her iki gerçek entry point'in de loop'u başlamadan önce çalıştığı
  an, yani hep no-op. Yeni `run_startup_backfill()`, `cli.py`'nin `_run_loop()`/`_run_voice_loop()`'u
  ve `api.py`'nin `lifespan()`'ı loop gerçekten ayaktayken çağırıyor. tok/s telemetrisi (`agent.py`)
  ham çıktı token sayısını hız sanıyordu — artık `on_llm_start`/`on_llm_end` arası gerçek
  `time.monotonic()` farkına bölünüyor. `.github/workflows/ci.yml` eklendi (ruff + pytest,
  `windows-latest` — `pywin32`/`winotify` Windows-only olduğu için; Flutter/Electron ayrı
  `continue-on-error` job). `requirements-lock.txt` eklendi (çalışan `.venv`'den `pip freeze` — tam
  bir `uv lock` resolution'ı bu bağımlılık ağacının ağırlığı/platform-özgüllüğü nedeniyle riskli
  görüldü). CI'yi yeşil başlatmak için `jarvis/`/`tests/` genelinde 38 kullanılmayan import/f-string
  temizlendi (ruff `--fix`, tamamı mekanik, davranış değişikliği yok — tüm testler yeşil kaldı).
  README'nin artık var olmayan `jarvis/legacy/` referansı silindi; CLAUDE.md'nin kendi stale OneDrive
  notu güncellendi (README/CONTRIBUTING zaten düzeltilmişti).
- **56 yeni test**: `test_api_security.py`, `test_confirmation_node.py`, `test_procedure_store.py`,
  `test_mcp_hardening.py`, `test_background_turn.py`, `test_shell_python_guard.py`,
  `test_hud_callback_tokrate.py`, `test_startup_backfill.py`. Tam suite: **160/160 yeşil** (3 test
  bazen tam suite altında flaky çıkıyor — timing-hassas background-task retry pencereleri, izole
  çalıştırıldığında hep geçiyor; bu oturumdan önce de var olan bir durum, kapsam dışı bırakıldı).

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
