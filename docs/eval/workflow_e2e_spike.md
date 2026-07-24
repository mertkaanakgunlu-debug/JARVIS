# Workflow E2E feasibility spike — Faz B0.2

> Owner-mandated pre-check (plan revision ②) before designing the "uzun workflow E2E" Gate Core
> scenario. Answers B0.2a-e from the approved plan. No code changed — pure exploration, this doc is
> the deliverable. Investigated: `jarvis/execution/workflow_engine.py`, `workflow_approval.py`,
> `jarvis/graph/tools.py` (`workflow_start`/`workflow_status`), `jarvis/graph/tool_router.py`,
> `jarvis/api.py` (`/workflow*` routes), `jarvis/cli.py` (`/workflow` command),
> `scripts/manual_test_driver.py`, `scripts/eval_oracle.py`.

## Verdict: feasible, fully HTTP-drivable — but needs a new evidence channel

The workflow engine's approval lifecycle is genuinely two-call, as the owner described
(`advance() → paused_for_approval → resolve_approval() → advance()`,
[workflow_engine.py:34-41](../../jarvis/execution/workflow_engine.py)), and it is **more complete and
more structured than the plan assumed** — every step of the lifecycle is reachable over the same HTTP
surface `manual_test_driver.py` already talks to (`--profile test`, no CLI-only dependency). The one
real gap: the oracle's existing evidence channel (`tool_trace.jsonl`) is **structurally blind** to
workflow step executions. This must be fixed in B1.2, not discovered mid-scenario-design.

## B0.2a — Is `workflow_start` actually callable by the model?

**Yes**, real `@tool`, appended to `make_tools()`'s returned list
([jarvis/graph/tools.py:1214-1311](../../jarvis/graph/tools.py), registration at line 1331). Built over
`_alpha_tools` only (never raw `all_tools`), so a workflow step can never target an alpha-disabled
capability. Gated **explicit-only**: `tool_router.py`'s `_EXPLICIT_ONLY_DOMAINS` includes `"workflow"`
([tool_router.py:50](../../jarvis/graph/tool_router.py)), triggered only by literal
`workflow`/`çok adımlı görev`/`multi-step` patterns (line 103) — an ambiguous keyword hit can never
expose it, by design (same precedent as `procedure_save`). **Scenario-design implication:** the
corpus prompt must use one of these trigger phrases explicitly, e.g. *"Şunu çok adımlı bir görev
olarak yap: ..."* — a natural-sounding multi-step request without the trigger phrase will NOT reach
`workflow_start` and would silently test the wrong path.

## B0.2b — Is `workflow_id` reliably obtainable by the driver?

**Yes, two independent ways.** `workflow_start` returns `engine.report(plan)` =
`render_workflow_report(plan)`, whose **first line** is
`f"[Workflow {plan.workflow_id} -- {plan.status}]"` ([workflow_engine.py:312](../../jarvis/execution/workflow_engine.py))
— reliably regex-extractable from the chat response (`\[Workflow (wf-[0-9a-f]+)`). Independently,
`GET /workflow` (list) returns real `workflow_id` fields per row via `workflow_store.list_workflows()`
([api.py:937-948](../../jarvis/api.py)) — the driver can also just take the most-recent row after
calling `workflow_start`. Recommend the regex extraction as primary (ties the id to the exact turn
that started it; no race with a concurrent workflow).

## B0.2c — How is `paused_for_approval` resolved — which API/CLI path?

**A real HTTP endpoint exists**, not just the CLI-only path the plan worried about:
`POST /workflow/{workflow_id}/resolve` with body `{"decision": "approve"|"deny"|"deny:<reason>"}`
([api.py:968-995](../../jarvis/api.py)). It calls the same shared
`jarvis.execution.workflow_approval.resolve_workflow_approval()` the CLI's `/workflow approve` command
uses ([cli.py:709-721](../../jarvis/cli.py)) — one service layer, two transports, no drift. Response is
structured JSON: `{"ok", "reapproval_required", "message", "status", "report"}` (api.py:989-995) — `ok`
and `status` are exactly what a driver-side `resolve_workflow(id, decision)` helper should assert on,
not the free-text `report`. Non-2xx on a hard failure (404 not-found, 400 invalid decision/state) —
easy for the driver's existing `post_json`/error-handling idiom to surface.

## B0.2d — Does `workflow_status` give a final state + step ledger?

**Yes, two forms.** The model-facing `@tool workflow_status(workflow_id)`
([tools.py:1313-1329](../../jarvis/graph/tools.py)) returns the same free-text
`render_workflow_report()` (step table + per-step `status`/`error`/`execution_id`/`compensation_note`,
[workflow_engine.py:278-298](../../jarvis/execution/workflow_engine.py)). The driver-facing
`GET /workflow/{workflow_id}` ([api.py:951-965](../../jarvis/api.py)) returns **structured** JSON:
`{"workflow_id", "status", "pending_approval_step_id", "report"}` — `status` is one of
`planned|running|paused_for_approval|succeeded|failed|partially_committed`
(`_finalize()`, workflow_engine.py:868-882). This structured `status` field, not text-parsing, should
be the oracle's primary success signal for a workflow scenario.

## B0.2e — Can the oracle verify success from the store/ledger, not just response text?

**Today, no — and this is the one real finding of this spike.** Confirmed by tracing the actual write
path: `WorkflowEngine._dispatch()` calls `self._audit("execution_start"/"execution_end", ...)` for
every step ([workflow_engine.py:816-840](../../jarvis/execution/workflow_engine.py)), and `_audit()`
writes those to **`audit_log.jsonl` only** — `tool_trace.record()` is called *inside `_audit()`* but
**only when `trace_block=True`**, which the execution_start/execution_end call sites never pass
(`trace_block` defaults `False`, workflow_engine.py:386-428). `trace_block=True` fires only for the
four `blocked_*`/`decision` outcomes in `_run_step`/`resolve_approval`.

**Consequence:** for a workflow step that actually *runs* — succeeds or fails, doesn't matter, as long
as it wasn't blocked — `tool_trace.jsonl` gets **zero rows**. Confirmed against every writer of
`tool_trace.record()` repo-wide: `agent.py:176`, `nodes.py:954,1010,1179,1204` (all chat-graph path),
and `workflow_engine.py:423` (block-only). And confirmed against the driver: `load_trace()`
([manual_test_driver.py:147-164](../../scripts/manual_test_driver.py)) reads *exactly*
`tool_trace.jsonl`, nothing else — there is no existing driver helper that reads `audit_log.jsonl` or
polls `GET /workflow/{id}`.

**Practical effect if ignored:** scoring a workflow scenario with today's `Expected.expected_tool` +
`eval_oracle._succeeded(trace, tool)` ([eval_oracle.py:97-100](../../scripts/eval_oracle.py)) would see
`trace=[]` for every step that actually ran, and always emit `"expected X to succeed; trace
tools=none"` — a **false FAIL every time**, regardless of whether the workflow actually worked. This
would have silently produced a permanently-red scored row if scenario design had started before this
spike.

### Required fix (scope for B1.2, not this spike)

Two additive, non-breaking changes needed before the workflow scenario can be scored honestly:

1. **Driver:** new `load_audit_log()` helper (mirrors `load_trace()`'s shape exactly, reads
   `HOME_DATA / "audit_log.jsonl"` instead), and a `get_workflow_status(workflow_id) -> dict` helper
   (`get_json(f"/workflow/{workflow_id}")`).
2. **Oracle:** `Observed` gains an optional `workflow_status: dict | None` field (or a parallel
   `audit_rows` list); `score()` gains a workflow-aware branch — e.g. a new `Expected.outcome` value
   `WORKFLOW_SUCCESS` that asserts `observed.workflow_status["status"] == "succeeded"` (or
   `"partially_committed"` where the scenario expects partial completion + honest compensation
   reporting), falling back to the existing trace-based checks for any pre-workflow chat turns in the
   same scenario (e.g. a setup turn before the workflow-triggering turn).

This is genuinely new oracle capability, not a reuse of the existing 13-scenario machinery — call this
out explicitly in B1.2's harness-wiring work so it isn't underestimated as "just add a row."

## Recommended scenario shape for B1.1

Given B0.2a's explicit-trigger requirement and B0.2e's evidence-channel fix:

```
Turn 1 (trigger workflow_start, workspace-only steps — no external_write, so it completes
        fully under --profile test):
  "Şunu çok adımlı bir görev olarak yap: önce notes.txt dosyasına 'toplantı notu' yaz,
   sonra o dosyayı oku, sonra içeriğini bir grafik başlığı olarak kullanarak plot_data ile
   bir grafik oluştur."
  → extract workflow_id from response (regex on "[Workflow (wf-...)")

Turn 2 (poll, no chat turn needed):
  GET /workflow/{workflow_id} → assert status == "succeeded"
  cross-check: load_audit_log() filtered by workflow_id has execution_end rows for
  file_write, file_read, plot_data, all ok=true — NOT the response text.
```

No approval pause needed for the Gate Core row (all three capabilities are L1/local — keeps the
scenario deterministic and fast for 10× runs); a **separate**, non-Gate-Core exploratory scenario
(Owner Extended corpus, B2) should exercise the `paused_for_approval` → `POST /resolve` path with a
step that genuinely requires confirmation, since that path is exactly what Faz A's live smoke is
independently verifying on the voice side — worth having a text-side equivalent, just not as one of
the 18-25 frozen Gate Core rows (a confirmation-requiring step adds a manual-approval round-trip to
every one of the 10 runs, which the acceptance matrix's cost note flags as disproportionate for a
frozen gate row).
