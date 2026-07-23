"""Human-only workflow approval resolution -- Faz 7.3 (P1).

ONE shared layer every transport goes through to approve/deny a paused
workflow step, instead of each surface reimplementing load -> resolve ->
advance with its own drift: the CLI's /workflow command and the API's
POST /workflow/{id}/resolve both call resolve_workflow_approval() below.
Approval stays deliberately NOT model-facing (no @tool wraps this --
exposing approve/deny to the agent would let it resolve its own
confirmation gate, the bypass this whole mechanism exists to prevent).

What this layer adds over calling WorkflowEngine directly:
  - Exact decision allowlist: only "approve", "deny", "deny:<reason>"
    (verb case-insensitive) are accepted. Anything else is rejected as an
    error -- never silently treated as an approval. (The engine's own
    historical rule "anything not starting with deny approves" made
    "yes"/""/"invalid" all approve.)
  - transport identity threaded into WorkflowEngine, whose own audit
    layer (Faz 7.3 P1, "workflow steps must not bypass the audit core")
    records the human decision (user_approved / user_denied /
    blocked_stale_approval) with workflow/step ids -- the engine is the
    single audit writer; this layer only supplies who/where.
  - The re-approval flow surfaced explicitly: a stale binding (process
    restart rotated approval.py's process-local HMAC key) re-issues a
    fresh request instead of executing or killing the workflow, and the
    outcome tells the caller to ask the human again.

Concurrency, stated honestly: within one process there is no await point
between approval.verify() and approval.consume() in the engine, so two
same-loop resolutions cannot both pass verification. ACROSS processes
(CLI process and API process resolving the same workflow simultaneously),
plan persistence is last-write-wins SQLite -- a sub-second simultaneous
approve in both could double-dispatch. That window is accepted for a
single-operator local assistant; the realistic cross-process case (a
stale terminal approving much later) is already caught by the fresh
load() here seeing the step no longer paused.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from jarvis.execution import workflow_store
from jarvis.execution.workflow import WorkflowPlan
from jarvis.execution.workflow_engine import WorkflowEngine, render_workflow_report


@dataclass
class ApprovalOutcome:
    ok: bool                    # the decision was accepted and acted on
    reapproval_required: bool   # stale binding -- a fresh request was issued, ask again
    message: str                # one-line human-facing outcome
    report: str                 # full render_workflow_report(), "" if no plan loaded
    plan: WorkflowPlan | None


def _invalid(message: str) -> ApprovalOutcome:
    return ApprovalOutcome(ok=False, reapproval_required=False, message=message, report="", plan=None)


async def resolve_workflow_approval(
    workflow_id: str,
    decision: str,
    *,
    tools: list,
    settings,
    workspace,
    transport: str,
) -> ApprovalOutcome:
    d = (decision or "").strip()
    dl = d.lower()
    if dl != "approve" and dl != "deny" and not dl.startswith("deny:"):
        return _invalid(
            f"invalid decision {decision!r}: must be exactly 'approve', 'deny', or 'deny:<reason>'"
        )

    # Review remediation (efficiency): workflow_store.load() is synchronous
    # sqlite3 I/O -- offload so an API-driven resolve doesn't stall the
    # event loop for any concurrently streaming SSE/voice client. Harmless
    # for the CLI's own call (a separate process/loop either way).
    plan = await asyncio.to_thread(workflow_store.load, workflow_id)
    if plan is None:
        return _invalid(f"workflow not found: {workflow_id!r}")
    if plan.status != "paused_for_approval" or not plan.pending_approval_step_id:
        return ApprovalOutcome(
            ok=False, reapproval_required=False,
            message=f"workflow {workflow_id} is not awaiting approval (status: {plan.status})",
            report=render_workflow_report(plan), plan=plan,
        )

    step_id = plan.pending_approval_step_id

    engine = WorkflowEngine(tools, settings, workspace, transport=transport)
    try:
        plan = await engine.resolve_approval(plan, step_id, "approve" if dl == "approve" else d)
    except ValueError as exc:
        return ApprovalOutcome(
            ok=False, reapproval_required=False, message=str(exc),
            report=render_workflow_report(plan), plan=plan,
        )
    plan = await engine.advance(plan)

    reapproval = (
        plan.status == "paused_for_approval" and plan.pending_approval_step_id == step_id
    )
    if reapproval:
        resolved_step = plan.step(step_id)
        return ApprovalOutcome(
            ok=False, reapproval_required=True,
            message=resolved_step.error
            or "previous approval was no longer valid; please re-approve",
            report=render_workflow_report(plan), plan=plan,
        )

    message = f"step {step_id!r} approved" if dl == "approve" else f"step {step_id!r} denied"
    return ApprovalOutcome(
        ok=True, reapproval_required=False, message=message,
        report=render_workflow_report(plan), plan=plan,
    )
