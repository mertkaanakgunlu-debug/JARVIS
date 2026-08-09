# J.A.R.V.I.S. — Safety & Confirmation Model

> Updated 2026-08-09. Phase 2 (ToolSpec metadata, commit `166a205`) and Phase 3
> (confirmation gate node, commit `7e7e471`) shipped 2026-05-24 but, per the 2026-07-14 review,
> didn't protect anything end-to-end. **Faz 4 closed that gap** — see "What Faz 4 changed" below.
> **Faz 5 extended it to a second, dynamically-discovered tool source (MCP)** without changing the
> gate itself at all — see "What Faz 5 changed" below. **Faz 7 added a second entry point into the
> graph (background-initiated, not user-typed turns) through the same gate, and surfaced a real
> (partially, not fully, mitigated) gap at the L2 level** — see "What Faz 7 changed" below.
> Treat this file as current; if it and the code disagree, trust the code and fix this file.

## Current safety mechanisms

| Mechanism | Where | What it covers |
|---|---|---|
| `DENY_PATTERNS` | `jarvis/tools/shell.py:9` | Blocks `rm -rf`, `format`, `del /f`, etc. in `shell_run` |
| System prompt rules | `jarvis/prompts/core/*.md` (see [prompt_loader.py](../jarvis/prompts/prompt_loader.py)) | "NEVER" directives (no hallucination, no fake data, etc.) — the pre-modularization `jarvis/prompts/system.md` pointer file was deleted in Faz 8 (2026-07-15) |
| `ToolSpec` risk metadata | `jarvis/tool_registry.py` | `risk_level` (L1-L3) + `requires_confirmation` per tool |
| **`policy_guard` kernel (Faz 4)** | `jarvis/policy_guard.py` | Transport-agnostic risk classification: per-*action* gating (not per-tool) for the four mixed-risk external_api tools, plus the kill-switch veto. Single choke point — the graph's confirmation node calls it. **Faz 5 confirmed the design worked as intended**: MCP tools (`jarvis/mcp_integration.py`) call the exact same `get_spec()`/`policy_guard.evaluate()` path with zero changes to this module — they're just more rows in `TOOL_SPECS`, added dynamically instead of statically. |
| **MCP tool gating (Faz 5)** | `jarvis/mcp_integration.py` (`_classify()`) | Every tool discovered from a connected MCP server gets a `ToolSpec` synthesized at connect time — fail-closed by default (L3 + `requires_confirmation=True`) unless explicitly known to be read-only/inconsequential-navigation (a short allow-list, e.g. Playwright's `browser_snapshot`). Registered into the same `TOOL_SPECS` dict via `tool_registry.register_dynamic_spec()`, so every mechanism in this table already covers it. |
| Confirmation gate | `jarvis/graph/nodes.py` (`make_confirmation_node`) | Interrupts the graph before tool calls `policy_guard` says need confirmation. **Now wired into all three interfaces** (see below) and **on by default**. |
| **Kill switch (Faz 4)** | `jarvis/kill_switch.py`, persisted at `data/kill_switch.json` | Emergency stop for external-effect (L3) actions specifically — file writes/todos/etc. (L2) are unaffected. Defaults to enabled (armed). `/killswitch off <reason>` (CLI) or `/killswitch on` to toggle; survives a process restart by design (it's a file, not a Settings field) — a trip stays tripped until someone deliberately re-arms it. |
| **Audit log (Faz 4)** | `jarvis/audit_log.py`, append-only at `data/audit_log.jsonl` | Two event kinds per risk_level ≥ 2 tool call: `decision` (policy_guard's ruling — auto_approved / confirm_required / user_approved / user_denied / blocked_kill_switch, written in `confirmation_node`) and `execution_start`/`execution_end` (the call actually ran, with outcome — written by `_HudEventCallback` in `jarvis/agent.py`, the same LangChain callback attached in `chat()`/`chat_stream()`/`resume_and_stream()`, so this fires for every transport). Never truncates/rewrites existing lines. |
| Meta-memory write-guard (Faz 2) | `jarvis/tools/files.py` (`PROTECTED_WRITE_PREFIXES`, checked in `write()`) | Unconditional, always-on — `file_write` raises `PermissionError` for any path under `jarvis/prompts/core/` (persona/safety directives), regardless of what the agent is instructed to do. Read access is unaffected. |
| SSRF guard (Faz 4) | `jarvis/tools/webfetch.py` (`_is_blocked_url`) | `url_read`/`deep_web_research` refuse to fetch localhost, private/link-local/reserved ranges, and cloud metadata endpoints (`169.254.169.254`) — checked against the *resolved* IP, not just the hostname string, so a public domain that DNS-rebinds to an internal address is still blocked. |
| `/system/wake` auth (Faz 4) | `jarvis/api_routers/system.py` | Now requires `X-API-Key` like every other mobile router; `/system/ping` stays auth-free by design (it's the liveness probe the mobile app uses to decide whether to *try* waking the PC). |
| Recursion cap (Faz 4) | `Settings.graph_recursion_limit` (default 30), passed as LangGraph's `recursion_limit` in `agent.py`'s `chat()`/`chat_stream()` config | A model stuck retrying a tool call fails — now caught as a controlled, ledger-based user message (Patch 1.2) instead of the opaque HTTP 500 it used to become. |
| Agent-node timeout (Faz 4) | `Settings.agent_llm_timeout_sec` (default 90s), `jarvis/graph/nodes.py`'s `make_agent_node` | A wedged provider connection surfaces as a clear in-conversation error instead of hanging the turn (and, in voice mode, leaving JARVIS listening in silence) forever. |
| **Deterministic tool-call limits (Patch 1.2)** | `Settings.max_tool_calls_per_ai_message` (4) / `_per_turn` (6) / `max_identical_tool_call` (1) / `max_tool_rounds_per_turn` (2), enforced at the top of `make_confirmation_node` **before** policy evaluation | LLM-independent, router-independent backstop against a hallucinated call storm. An over-limit batch is rejected WHOLE (executing "just the first N" would be guessing which part of a hallucination is safe). Motivated by live incident A2 (a local model emitted ~20 calls incl. 2 email sends off a one-line smalltalk turn — only the external-write gate stood in the way). Blocked calls still count toward the counters; each block is audited with `batch_size`/`turn_attempted_count`/`unique_tool_count`/`external_write_count`. |
| **Tool-call dedup (Patch 1.2)** | `seen`/`completed` fingerprint sets in graph state; `jarvis/graph/tool_accounting.py` | The same `tool + normalized-args` fingerprint never executes twice in a turn — a repeat is stubbed with `[DUPLICATE_TOOL_CALL_BLOCKED]`, whether the first attempt succeeded or failed. `completed` is stamped only by the post-execution `tool_result_accounting` node (the confirmation node can't know an outcome yet). Motivated by live incident F16 (procedure_save re-issued ~10× after already succeeding). |
| **Tool error boundary (Patch 1.2)** | `jarvis/graph/safe_tools.py` (`SafeToolNode`) | A tool body raising (missing dep, bad credential, timeout, …) becomes a structured `[TOOL_ERROR]` ToolMessage the model can react to, not a crashed graph run / HTTP 500. The error text is sanitized to one line — never a `repr()`/traceback, which could leak a credential, token, or path into the transcript. |
| **Bare compose node (Sprint 2)** | `jarvis/graph/nodes.py` (`make_compose_node`), wired `tools→…→compose→critic` | The node that writes the final user-facing answer has NO tools bound, so it structurally cannot re-issue the call it just watched run. This is the structural (not prompt-based) fix for F16's `agent→procedure_save→agent→…` loop; re-entering the tool-bound agent is allowed only for multi-step-shaped turns within the round budget. |
| **Capability router / turn-scoped tools (Sprint 2)** | `jarvis/graph/tool_router.py`; `ToolSpec.domain` | The model sees only the ~0–8 tool schemas relevant to the turn's domain, not all ~36. Not itself a safety gate, but it shrinks the hallucination surface that the limits above defend, and it keeps MCP/browser tools quarantined behind explicit intent. |

## Per-action, not per-tool (BUG-6 — fixed)

Several tools dispatch on an `action` argument and mix reads with writes under one `ToolSpec`.
`ToolSpec.actions` (populated from `tool_registry._TOOL_ACTIONS`) downgrades the documented read
actions back to L1/no-confirm; an action that is not listed keeps its tool's risk, which is always
the stricter answer, so a forgotten action — or one added later — fails safe.

**Post-MVP Faz 2.75 (Paket E) widened this past the four external tools it used to cover.** The
table lived in `policy_guard._READ_ACTIONS` and listed only `google_calendar`, `gmail`,
`google_drive` and `itu_mail`. `todo`, `schedule` and `finance` are L2/`local_write` at the tool
level, so their pure reads were classified as writes — and the proactive read-only clamp fires on
`risk_level >= 2 and not requires_confirmation`, which every one of them matched. Measured
2026-08-01: `todo("list")`, `schedule("list")` and `finance("summary")` were **blocked** on a
self-initiated turn while `google_calendar("list")` and `gmail("list_unread")` passed. A briefing
running on that path could read the calendar and the mailbox and not the to-do list.

Read `todo`'s and `schedule`'s `done` before editing the table: `todo("done")` MARKS a task
complete (a write); `schedule("done")` LISTS completed tasks (a read). Same word, opposite
operation — which is why the table is per-tool rather than a global set of read-ish verbs.

## Kill switch vs. confirmation gate — different jobs

- **Confirmation gate**: asks the user, per call, before a risky action runs. Needs an interactive
  loop to answer it (CLI text prompt, spoken yes/no over voice, or a client hitting
  `POST /chat/confirm/{conf_id}`) — a background `TaskExecutor` job has no such channel, so a task
  that hits a confirmable action fails clearly instead (`jarvis/task_executor.py`'s
  `ConfirmationRequired` handling) rather than hanging or silently proceeding.
- **Kill switch**: a blanket "no" for every L3 (external-effect) action, with no prompt at all —
  the point is an emergency stop that works even if the interactive loop asking questions is itself
  the thing behaving badly. Checked first, inside `confirmation_node`, before the gate's own
  enabled/disabled state is even consulted.
- **Kill switch failure policy (2026-07-19 hardening)**: a state file that *exists but cannot be
  read/parsed* (torn write, encoding damage, wrong schema) is treated as **TRIPPED — fail-closed**,
  with a once-per-episode CRITICAL log and a structured `audit_log` event
  (`kill_switch_state_unreadable`), instead of the old silent fallback to the stale in-memory cache
  or the armed default (both silent directions were the 2026-07-18 BOM incident). A *missing* file
  is not corruption: cold-start default stays armed-off `enabled=True` (fresh install), and deleting
  the file mid-run does not silently re-arm a live trip (last known-good cache answers). The
  synthetic trip is never cached, so a fixed file — or `/killswitch on|off`, which rewrites a valid
  file even over a corrupt one — takes effect on the very next call.

## What Faz 4 changed (previously: "Known gap")

- `confirmation_gate_enabled` now defaults to **`True`** (`jarvis/config.py`) — was `False`.
- All three interfaces now handle an interrupted call:
  - **CLI text** (`jarvis/cli.py`): catches `ConfirmationRequired`, shows what's pending, prompts
    y/n (+ optional reason), resumes via `agent.resume_and_stream()`.
  - **Voice** (`jarvis/cli.py`'s `--voice` loop, `jarvis/voice_api.py`'s wakeword/PTT loop and the
    `/ws` remote-audio session in `jarvis/api.py`): `chat_stream()`'s `__jarvis_confirm__` JSON
    marker is detected (`jarvis/voice/session.py`'s `parse_confirm_marker`) instead of being spoken
    verbatim, replaced with a natural spoken question, and the *next* utterance is treated as the
    yes/no answer (anything not recognized as affirmative denies — fail-safe, same default as the
    CLI prompt's `default="n"`).
  - **API**: `POST /chat` now catches `ConfirmationRequired` and returns
    `{"confirmation_required": true, "id": ..., "payload": ...}` instead of an opaque 500
    (BUG-confirm-payload); `POST /chat/stream` already carried the JSON marker through as an SSE
    frame. The Electron HUD builds a real UI around this since 2026-07-23 (`52d0b72`), and the
    Flutter app now has the same approve/deny capability — see Known limits below.
- The system prompt (`jarvis/prompts/core/02_tool_policy.md`) no longer tells the model it never
  needs to ask — it now says the system itself pauses for risky actions and describes how to react
  to an approval/denial coming back.
- `python_run` reclassified L2→L3 + `requires_confirmation=True` (BUG-1) — it was more powerful
  than `shell_run` (arbitrary unsandboxed Python from any absolute path) while sitting at a lower
  gate. **This is an access-control fix, not a sandbox** — the subprocess itself still has no
  resource/network restrictions; true sandboxing is a deferred hardening item, not implemented.

## What Faz 5 changed

- New `jarvis/mcp_integration.py` connects to configured external MCP servers (disabled by
  default) and merges their tools into the graph as a second, dynamically-discovered tool source.
  Ships with one real server: Microsoft's official Playwright MCP (real browser automation —
  navigate/click/type/snapshot/screenshot/evaluate JS/…), flip `MCP_PLAYWRIGHT_ENABLED=True` to
  enable it.
- **A browser tool is a materially bigger step than the existing `web_search`/`url_read`** — both
  already feed untrusted web page text to the model (an existing, unchanged prompt-injection
  surface), but neither gives the model the ability to *act* on a page. Playwright does: a poisoned
  page's content could get the model to *decide* to click/submit/type something. The mitigation is
  the same gate this whole document describes, just applied here: every Playwright tool beyond pure
  inspection/navigation (click, type, fill_form, select_option, file_upload, drag, drop, hover,
  handle_dialog, evaluate, run_code_unsafe) defaults to L3 + `requires_confirmation=True`, so the
  model deciding to click something and the click actually happening are still separated by the
  user approving that specific, described call.
- `policy_guard.describe_call()`'s `_DETAIL_KEYS` gained `element`/`url`/`text` — without this, a
  pending `browser_click` confirmation would have shown just the bare tool name with no indication
  of what was about to be clicked, undermining the informed-consent point of asking at all.
- No change to the gate's mechanics, the kill switch, or the audit log — see the `policy_guard`
  kernel row above for why none of those needed to change.

## What Faz 7 changed

- New `JarvisAgent.proactive_turn()` — a second entry point into the compiled graph, alongside
  `chat()`/`chat_stream()`, for **background-initiated** turns (`jarvis/monitor.py` deciding "is
  this new email/event worth surfacing?"), not user-typed ones. Runs the exact same graph, same
  `policy_guard`/kill-switch/audit_log gate — **zero changes to any of them**, same principle as
  the MCP layer (Faz 5): a new tool/transport source is just more traffic through one existing
  choke point, not a reason to add a second one. Off by default
  (`Settings.monitor_proactive_enabled=False`).
- **Confirm-or-notify, not silent execution, for L3**: `proactive_turn()` can never raise
  `ConfirmationRequired` the way `chat()` does — there's no interactive channel for a background
  thread to answer it (same constraint `TaskExecutor` already has, see above). If the graph
  interrupts for an L3 action, the pending confirmation is discarded (never resumed, never silently
  executed) and reported back as `kind="needs_confirmation"`; `monitor.py` turns that into a toast
  naming the gated tool(s) and telling the user to ask JARVIS directly, instead of leaving a
  confirmation dangling for an interactive client to resolve through
  `POST /chat/confirm/{conf_id}`.
- **Live finding, not theoretical — a real gap at L2, honestly documented, not fully closed**: a
  real verification run against local `qwen2.5:7b-instruct` gave `proactive_turn()` a mundane
  calendar-event trigger and the model hallucinated an unrelated `procedure_save` call. That tool is
  `risk_level=2` (`local_write`, `requires_confirmation=False`) — by this document's own existing,
  deliberate design ("kill switch is L3-only"), L2 writes bypass the gate for a normal, human-driven
  turn, where a person is present to notice and course-correct. A background self-check has nobody
  watching, so this is a real behavioral gap Faz 7 newly exposes (not introduces — the L2 no-gate
  design already existed): a misjudging or hallucinating model can cause a silent local side effect
  (`procedure_save`, `todo`, `file_write`, `spotify`, ...) with no confirmation and no one present to
  catch it. **Mitigated, not eliminated**: `_proactive_system_prompt()` (`jarvis/agent.py`) now
  explicitly forbids calling any creating/saving/sending/modifying tool during a proactive check —
  investigation must stay read-only, a suggested action belongs in the reply text, not a live tool
  call — and a re-run against the same model with the same trigger no longer reproduced the
  hallucinated call. This is a prompt-level mitigation on a non-deterministic model, not a structural
  guarantee the way the L3 gate is a structural guarantee — it narrows the risk, it doesn't
  close it the way policy_guard closes the L3 case. A structural fix (e.g. a reduced,
  read-only-only tool set specifically for proactive turns) would close it properly but wasn't
  built this phase — flagged here rather than left undiscovered.
- **`--monitor` now actually starts in `--api` mode** (`api.py`'s `lifespan()`) — previously silently
  ignored there; only the CLI branch ever constructed a `JarvisMonitor`. Matters here because the API
  server, not an interactive CLI session, is where this new autonomous entry point runs continuously
  in practice.

## What the stabilization sprint changed (2026-07-16)

- New `Settings.external_writes_enabled` (default `True`, no behavior change for normal runs).
  When `False` — set automatically by `python -m jarvis --profile test` — `make_confirmation_node`
  hard-denies any tool call that would write externally (gmail send, calendar create/delete, Drive
  upload/share/delete, ITU mail send, Spotify) **before it ever reaches the interrupt**, same
  "no prompt, no execution" shape as the kill switch above, just narrower in scope (external writes
  only — local writes/shell/python stay reachable so tool-calling itself remains testable under the
  profile). Patch 1.1 (2026-07-16): the gate is keyed on the per-CALL
  `PolicyDecision.side_effect_type` — the mixed read/write tools' read actions resolve to
  `"external_read"` and pass, so `gmail read` / `calendar list` / `drive download` still work under
  the profile (the original static `ToolSpec` check denied those too). Exists specifically so a
  scripted/CI test run can never accidentally send a real email or touch a real calendar, without
  needing a human to answer a confirmation prompt that isn't there.
- Unrelated to the gate itself, but relevant to trusting what you see while testing it: the model
  label and cost shown in `/status`/`current_model_label` used to be derived from the *requested*
  role, not the provider that actually answered — a turn served by the Ollama fallback could still
  show "Gemini (Vertex, reasoning)" and a nonzero cost. `jarvis/llm_trace.py`'s `LlmTraceRecorder`
  now reports the real per-call provider/model/cost — see `CHANGELOG.md`'s stabilization-sprint entry
  for the mechanism. Doesn't change what the gate blocks, only whether you can trust what you're
  told happened.

## What Post-MVP Faz 1 changed (2026-07-31) — the honesty kernel

A different axis from everything above. Faz 4–7 answer *"is JARVIS allowed to do this?"*; this
phase answers *"did JARVIS actually do what it just told you it did?"* A confirmation gate cannot
help with a model that reports a chart it never drew — no tool call was made, so there was nothing
to gate.

Two gates were holding the existing verification machinery shut. `VerifiedExecutionSummary`
(`jarvis/execution/summary.py`) had been wired into `compose_node` since Agent Runtime rev.2 Faz 4,
but `execution_contract_mode` defaulted to `"off"`, **and** the summary was only built when the
turn's envelope list was non-empty — so the one case that matters most, a turn where no tool ran at
all, skipped every check. Both are now open.

| Mechanism | Where | What it covers |
|---|---|---|
| **Artifact declaration** | `jarvis/execution/artifacts.py`, declared inside `generate_plot` / `export_cashflow_workbook` / `latex_write` / `compose_report` / `latex_compile` | A tool that writes a file states the real, final path at the moment it is on disk. Rides back on `ToolMessage.artifact` (out of band — the model-facing return string is untouched), so nothing has to parse prose. Before this, `file_write` was the only tool in the registry that could be verified at all, because it is the only one whose output path is a direct argument. |
| **`declared_artifacts_exist` postcondition** | `jarvis/execution/postcondition_runner.py`, attached to `plot_data`, `report_write`, `report_compose`, `report_compile`, `finance` | Every declared file is checked on disk. Present → `confirmed`; **declared but absent → the tool's self-reported success is downgraded to "reported successful … but independent verification FAILED"**, which `render_operation_status_for_user` puts in front of the user. Nothing declared → `unverified`, never a free pass. |
| **Unbacked-claim gate** | `jarvis/execution/evidence.py`, run by `verification_node` — a **terminal** graph node on every path to END | Catches the zero-tool class: the answer names a file that is neither a declared artifact of this turn nor present on disk, or asserts a side effect (*saved / sent / exported*) in a turn where nothing ran. Typed evidence only — no number scanning, which the external review showed manufactures false positives out of ordinary sentences. In enforce mode it runs **exactly one** bounded repair round, then falls back to an honest report. |
| **Rollout metrics** | `jarvis/execution/rollout.py`, append-only at `data/execution_verification.jsonl` | Makes the shadow→enforce promotion a measurement instead of a judgment call: 100 real artifact operations with zero reported false blocks. Read it with `python scripts/verification_status.py`; record an operator-judged false block with `--mark-false-positive "reason"`. |

**The default is now `shadow`, not `off`.** In shadow everything above is computed and counted, and
the user-visible answer is not touched — a detector bug costs a log line, not a wrong reply.
`off` remains the rollback switch.

Two honesty notes about this section itself:

- `false_positive_known` is **never inferred**. A false positive means verification contradicted a
  claim that was actually true; if code could detect that, it would not have fired. Zero means
  "none reported", not "none occurred", and `enforce_gate_status()` says so in its own output.
- The gate is tuned for precision over recall, deliberately: it stays silent on anything it cannot
  prove is false, including a named file that really is on disk (the user may be asking about
  something written last week). It will miss hallucinations. It is not supposed to block a claim it
  cannot contradict with evidence.

## What Post-MVP Faz 2 changed (2026-07-31) — confidence-based calendar confirmation

The first change in this codebase that **removes** a confirmation prompt, so it is written up in
more detail than its size suggests.

The owner's decision was *"takvim/todo serbest — tarih hatası kapandıktan sonra"*: stop
interrupting for routine calendar entries once dates can be trusted. Faz 2 is where that date bug
closes (`jarvis/clock.py` — relative dates were resolved against UTC and handed to Google as local
wall-clock, so for three hours a day "yarın" landed a day early). The plan deliberately does not
cash the decision in as a blanket relaxation: *"JARVIS may create calendar events without asking"*
and *"JARVIS may create calendar events it is not sure about without asking"* are different
promises.

```
create + high confidence          -> runs, no prompt
create + ambiguous date/time/title -> asks
batch_create | update | delete     -> asks, always, however confident
```

Only a single `create` is eligible. `batch_create` writes N events from one approval, `update`
silently rewrites something that already exists, and `delete` is the one genuinely hard-to-reverse
action in the tool. A single create is the only one a user can undo by looking at their calendar.

**Confidence is derived, never supplied.** `policy_guard.calendar_confidence()` computes it from
the arguments and from the user's own sentence. If it were a tool argument, the model could set it
to 1.0 and approve its own actions. It is the minimum across date, time, title and utterance — an
event at a confidently wrong hour is as wrong as one on a confidently wrong day.

**Reading the user's sentence is load-bearing, and live measurement is why.** Scoring the
arguments alone is not enough: asked *"Pazartesi saat 4'te spor salonu diye takvime bir şey
ekle"*, real qwen3:8b ignored the instruction to pass the wording through, resolved the weekday
itself — to a Saturday — and passed an ISO date. Scored on args alone that is a 1.00, and a
wrong-day event would have been created silently. The gate now also reads `state["user_query"]`,
which can only ever LOWER confidence, and blocks outright on a provable contradiction (the user
named a weekday the given date is not).

### What this did NOT relax

Every one of these was written when `risk_level >= 3` implied `requires_confirmation=True`. Faz 2
is the first thing that breaks that equivalence, so each was re-checked against a call that
auto-approves, and each has a test in `tests/test_calendar_autonomy.py`:

| Mechanism | Status |
|---|---|
| **Kill switch** | **This was a real bypass and had to be fixed.** The veto read `requires_confirmation and risk_level >= 3`; an auto-approved create would have walked straight past a tripped switch. Now keyed on `risk_level` alone — a no-op for every pre-existing tool, since the registry sweep already guarantees L3 ⇒ confirmation. |
| **Risk classification** | Unchanged. Still L3, still `external_write`. Confidence buys not being asked, nothing else. |
| **Audit log** | Unchanged — the filter is `risk_level >= 2`, so the row is still written, with `outcome="auto_approved"` and the confidence in `reason`. "Not asked about" never means "not recorded". |
| **`--profile test`** | Unchanged. Keys on `side_effect_type`, which Faz 2 does not touch. |
| **Background / proactive turns** | Never reach the downgrade: `interactive` is derived from the transport, and a `monitor-*` turn is not interactive. Such a turn still takes the pre-existing L3 interrupt→notification path. |
| **Workflow engine, and any other caller** | Unchanged. `interactive` defaults to `False`, so a caller that has not thought about it keeps pre-Faz-2 behaviour. |
| **Mail and Drive** | Unchanged and explicitly out of scope — *"Mail hep onaylı"*. |

The confirmation prompt itself also improved: it now names the **resolved** instant
(`when=2026-08-02 15:00`) rather than the expression (`date=yarın`). A user cannot tell from the
word whether it resolved to the right day, which is how a one-day-early event gets approved.

## Known limits (honest, not aspirational)

- **Electron and mobile confirmation mechanics are live-verified; Electron's
  approve-side final response remains open.** The Electron HUD ran against a
  real server, graph, and `BrowserWindow`: the card rendered, no raw protocol
  leaked, approve produced exactly one execution, and deny produced zero. The
  explicit-deny narration bug is fixed, but an approved clean execution can
  still receive fabricated uncertainty in the model's free-text final answer;
  result binding on that path remains open. The Flutter UI also ran against the
  real server and model on a Galaxy S26 Ultra (`SM-S948B`): exactly one
  execution on approve, zero on deny, and no raw protocol on screen. Mobile's
  remaining confirmation limitations are the missing cross-tab indicator and
  missing `conversation_id`, not an unrun live E2E.
- **Voice confirmation phrasing is functional, not fully localized** — the spoken question wrapper
  is bilingual (`jarvis/voice/session.py`'s `describe_confirmation`), but the per-call description
  embedded in it (`jarvis/policy_guard.py`'s `describe_call`) is always in English technical form
  (e.g. "send an email (to=alice@x.com, subject=Toplantı)"), even in a Turkish session.
- **A background `TaskExecutor` job cannot answer a confirmation** — by design (see above), not a
  bug, but worth knowing: an async-submitted query that turns out to need one just fails with an
  actionable error telling the user to ask interactively instead.
- **`python_run`'s reclassification is a gate, not a sandbox** (see above).
- **The honesty kernel is in shadow, so today it observes rather than blocks.** Everything in
  "What Post-MVP Faz 1 changed" is computed on every turn, but only an `enforce_*` mode rewrites a
  contradicted answer. Until the promotion gate is met, a fabricated claim is *detected and logged*,
  not withheld from the user.
- **Verification covers five artifact tools, not all 36.** `plot_data`, `report_write`,
  `report_compose`, `report_compile` and `finance` declare; `file_write` keeps its own stronger
  args-resolved checks. Every other tool still reports honestly as "not independently verified" —
  which is accurate, not a silent pass, but it is not coverage.
- **A tool could declare a path it did not write.** The declaration is made by the tool itself, so
  it is trusted the same way the tool's return value is; the postcondition proves the file exists,
  not that this call is what produced it.
- **On a streaming transport, an enforce-mode block cannot un-send the draft.** The gate runs after
  `compose_node`'s answer is produced, but the HUD/voice stream has already emitted it token by
  token. The repair round itself is tagged (`REPAIR_STREAM_TAG`) so it never splices onto the draft
  — but the corrected answer only reaches the user through the node's returned state and the saved
  history, not through the stream they already watched. Buffering compose output until verification
  finishes is the real fix; **this is a blocker on the shadow→enforce promotion**, listed with the
  metric threshold, not after it. In shadow (today's default) nothing is rewritten, so nothing
  diverges.
- **Kill switch scope is L3 only** — it does not block L2 (reversible local writes: `file_write`,
  `todo`, `spotify`, ...). This is deliberate (an emergency stop for JARVIS acting on the *outside
  world*, not a full halt of all functionality) but worth knowing if you expected it to block more.
- **A calendar `create` can now happen without a prompt** (Faz 2, above). It is bounded — single
  create only, high confidence only, human present only, kill switch and audit unchanged — but it
  is a genuine reduction in how often the user is asked, and `calendar_autonomy_enabled=False`
  restores the old behaviour exactly if that turns out to be the wrong trade.
- **The weekday cross-check errs toward asking.** If the user names a weekday and the model's date
  is a different weekday, the call is blocked from auto-approving. When the sentence carries its own
  date expression the check is skipped (so "Cuma raporu için yarın toplantı" is fine), but a
  weekday word used as a *name* with no other date in the sentence will still cost one confirmation
  prompt. That is the safe direction, and it is a false-positive class, not a false-negative one.
- **Nothing checks that the model relayed the user's wording faithfully.** The gate reads
  `state["user_query"]`, so it catches an ambiguous request laundered into confident arguments. It
  cannot catch a model that invents a detail the user never said in a sentence with no temporal
  ambiguity at all — the title and description discipline is subtractive only, and "was this
  description actually said?" is not knowable from the tool arguments.
- **Person-name resolution is built and tested but not yet called from any live path.**
  `jarvis/nlu/entities.py` resolves "Baranla" → "Baran" only when a source corroborates the stem,
  with bands that decide between normalising, asking, and leaving the input alone. Nothing invokes
  it yet, and that is deliberate rather than unfinished: the only source trusted enough to
  auto-normalise an inflected form is Google Contacts, which is off by default
  (`google_contacts_enabled`) because enabling it costs one OAuth re-consent — an owner decision.
  Wiring it to the calendar path before that source exists would ship a code path whose every
  outcome is "leave it alone", which is not a behaviour worth testing in production. **Do not read
  this as "names are being corrected today": they are not.**
