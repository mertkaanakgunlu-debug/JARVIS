"""WorkflowEngine -- Agent Runtime rev.2, Faz 7 (Workflow runtime).

Drives a WorkflowPlan's steps to completion. Deliberately SEPARATE from the
single-turn chat graph (jarvis/graph/graph.py's StateGraph) -- the plan's own
words: "Tek-turn chat graph ile workflow engine ayrilir; workflow Faz 1-4'un
execution contract'ini yeniden kullanir, paralel evren kurmaz." Concretely:
this module has no LangGraph node/edge of its own and never runs inside a
compiled graph; each step is dispatched directly against the same tool
objects jarvis.graph.tools.make_tools() builds, through the exact same
validate -> risk-classify -> (confirm) -> execute -> verify pipeline
jarvis/graph/nodes.py's prepare_execution_node/confirmation_node/
tool_result_accounting already implement for the single-turn path -- reused
via jarvis.execution.{request,approval,idempotency,postcondition_runner,
envelope,summary}, not reimplemented (plan principle #2: one verification
vocabulary).

Reuses two small helpers from jarvis.graph.tool_accounting / .safe_tools
(tool_call_fingerprint, content_is_failure, parse_blocked_code,
parse_timeout_flags, format_tool_error) rather than forking parallel
copies -- confirmed this does not introduce an import cycle: those two
modules only import jarvis.execution.*/jarvis.tool_registry/jarvis.graph.state,
never jarvis.graph.nodes or this module.

Step lifecycle (jarvis.execution.workflow.StepStatus):
  pending -> running -> succeeded | failed
                     \\-> needs_approval -> (resolve_approval) -> running -> ...
  pending -> skipped   (propagated from a failed/skipped dependency)
  succeeded -> compensated   (see compensate() below)

Approval pause: mirrors confirmation_node's HMAC-bound ExecutionRequest
(jarvis.execution.request/approval) exactly, but pauses by returning from
advance() with plan.status="paused_for_approval" and persisting, rather
than a LangGraph interrupt() -- there is no compiled graph here to interrupt.
A caller resumes with resolve_approval(plan, step_id, "approve"|"deny[:why]"),
then must call advance() again to keep going (same two-call shape the
existing confirmation interrupt/resume + graph-continues pattern already
has, so it should feel familiar to anyone who has touched confirmation_node).

Crash recovery: a step found "running" when a plan is loaded (advance()
calls _recover_interrupted_steps() first) means the process died mid-
dispatch. idempotency.is_committed(step.execution_id) is the ground truth
(idempotency.py's own documented semantics: a journal hit ONLY ever means a
genuine committed side effect) -- committed -> mark succeeded (honestly,
without a fabricated envelope); not committed -> reset to "pending", safe to
retry from scratch since it never actually completed.

Compensation (plan section, Faz 7 bullet 4) is deliberately narrow: "yalniz
kayitli gercek tersi olan islemlerde otomatik telafi" -- only two real
compensators are registered (_COMPENSATORS below): file_write (restore
previous content, or delete a newly-created file) and todo's "add" action
(delete the created to-do, id recovered from its own result text). Every
other capability's succeeded steps are left exactly as they are, honestly
uncompensated -- no generic rollback is claimed or attempted, per the plan's
explicit rejection of a general workflow rollback/compensation promise.

Scope cuts, stated up front:
  - No LLM-driven replanning: replan() enforces max_replans as a real budget
    and appends caller-supplied steps, but nothing here decides WHEN a
    replan is needed or WHAT the new steps should be -- same "mechanism
    before trigger" precedent as Faz 1's shadow ledger preceding Faz 2's
    first real decision from it.
  - Not replicated from confirmation_node: batch-size/duplicate-fingerprint
    dedup (a workflow step is dispatched one at a time, already uniquely
    identified by step_id, and the idempotency journal is the stronger
    duplicate-execution guard) and the proactive-turn read-only gate (no
    proactive/background trigger exists for a workflow in this phase).
    external_writes_enabled (the --profile test structural guarantee) IS
    replicated -- a workflow step must not be able to bypass that isolation.
  - No live trigger yet: nothing constructs a WorkflowPlan from a real user
    request or wires WorkflowEngine into JarvisAgent/the tool registry as a
    callable capability. This phase builds and tests the engine as a
    standalone, directly-invokable mechanism -- the same "Part 1: shapes and
    mechanism, Part 2: wire it to something live" split Faz 1->2 and Faz 6
    Part 1->2 already used.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from jarvis import audit_log, policy_guard
from jarvis.execution import approval, idempotency, workflow_store
from jarvis.execution.redaction import redact_preview
from jarvis.execution.args_schemas import validate_args
from jarvis.execution.contract import TaskContract
from jarvis.execution.envelope import build_shadow_envelope
from jarvis.execution.postcondition_runner import run_postconditions
from jarvis.execution.request import ExecutionRequest, resolve_target_resource
from jarvis.execution.summary import build_verified_summary, render_operation_status_for_user
from jarvis.execution.workflow import WorkflowPlan, WorkflowStep
from jarvis.graph.safe_tools import format_tool_error
from jarvis.graph.tool_accounting import (
    content_is_failure,
    parse_blocked_code,
    parse_timeout_flags,
    tool_call_fingerprint,
)
from jarvis.tool_registry import get_spec

_DEFAULT_TIMEOUT_SECONDS = 60.0
# Mirrors safe_tools.py's own _HARD_PROCESS_BUFFER_SEC -- the primary kill
# mechanism for a hard_process_timeout tool is its OWN internal
# subprocess.run(timeout=...); this outer bound deliberately trails it
# rather than racing it (see that module's comment for the full reasoning).
_HARD_PROCESS_BUFFER_SEC = 10.0


def _timeout_bound_for(spec) -> float:
    if spec is None:
        return _DEFAULT_TIMEOUT_SECONDS
    if spec.timeout_class == "hard_process_timeout":
        return float(spec.timeout_seconds) + _HARD_PROCESS_BUFFER_SEC
    return float(spec.timeout_seconds)


# ── Compensation registry ────────────────────────────────────────────────────

def _compensator_key(capability: str, args: dict[str, Any]) -> tuple[str, str | None]:
    action = args.get("action")
    return (capability, action.strip().lower() if isinstance(action, str) else None)


def _capture_file_write(args: dict[str, Any], workspace: Path) -> dict[str, Any] | None:
    """Snapshot a file's content BEFORE file_write overwrites it, so
    compensate() has something real to restore. Reuses jarvis.tools.files'
    own path resolution (_resolve) rather than re-deriving the target path
    with different logic that could disagree with what write() itself will
    actually touch."""
    from jarvis.tools.files import _resolve

    path_str = str(args.get("path", ""))
    if not path_str:
        return None
    try:
        path = _resolve(path_str, workspace)
    except Exception:
        return None
    if path.exists() and path.is_file():
        try:
            return {"path": str(path), "existed": True, "previous_content": path.read_text(encoding="utf-8")}
        except (OSError, UnicodeDecodeError):
            return {"path": str(path), "existed": True, "unreadable": True}
    return {"path": str(path), "existed": False}


async def _compensate_file_write(
    step: WorkflowStep, content: str, workspace: Path, tools_by_name: dict
) -> str:
    captured = step.compensation_data
    if not captured:
        return "no capture data -- cannot compensate"
    path = Path(captured["path"])
    if captured.get("unreadable"):
        return f"cannot compensate: previous content of {path} was not text-readable at capture time"
    if not captured.get("existed"):
        try:
            path.unlink(missing_ok=True)
            return f"deleted newly-created file {path} (no previous version existed)"
        except OSError as exc:
            return f"failed to delete {path}: {exc}"
    try:
        path.write_text(captured["previous_content"], encoding="utf-8")
        return f"restored previous content of {path}"
    except OSError as exc:
        return f"failed to restore {path}: {exc}"


# to-do add's own result text: "eklendi [<id>]" (jarvis/graph/tools.py) --
# same "recover a structured fact from a tool's own free-text result"
# pattern postcondition_runner.py's _check_exit_code_matches already uses.
_TODO_ADDED_ID_RE = re.compile(r"eklendi \[([0-9a-f]+)\]")


async def _compensate_todo_add(
    step: WorkflowStep, content: str, workspace: Path, tools_by_name: dict
) -> str:
    match = _TODO_ADDED_ID_RE.search(content or "")
    if not match:
        return "cannot compensate: could not recover the created to-do's id from its result text"
    tool = tools_by_name.get("todo")
    if tool is None:
        return "cannot compensate: 'todo' tool not available in this engine instance"
    todo_id = match.group(1)
    try:
        result = await tool.ainvoke({"action": "delete", "todo_id": todo_id})
    except Exception as exc:
        return f"compensating delete failed: {exc}"
    result_text = result if isinstance(result, str) else str(result)
    if content_is_failure(result_text):
        return f"compensating delete reported failure: {result_text[:150]}"
    return f"deleted to-do {todo_id} created by this step"


_CaptureFn = Callable[[dict, Path], "dict | None"]
_CompensateFn = Callable[[WorkflowStep, str, Path, dict], Awaitable[str]]

_COMPENSATORS: dict[tuple[str, str | None], tuple[_CaptureFn, _CompensateFn]] = {
    ("file_write", None): (_capture_file_write, _compensate_file_write),
    ("todo", "add"): (lambda args, workspace: None, _compensate_todo_add),
}


# ── Reporting ─────────────────────────────────────────────────────────────────

def render_workflow_report(plan: WorkflowPlan) -> str:
    """Human-facing summary of a plan's current state -- reuses
    jarvis.execution.summary's existing VerifiedExecutionSummary renderer
    over the plan's own collected step envelopes (plan principle #2: one
    verification vocabulary, not a second one for workflows). Module-level
    (not a method) so a read-only caller -- workflow_status's @tool body,
    the CLI's /workflow command -- can render a report from a loaded
    WorkflowPlan alone, without constructing a full WorkflowEngine (which
    needs a live tools list) just to read one."""
    envelopes = [s.envelope for s in plan.steps if s.envelope is not None]
    summary = build_verified_summary(envelopes)
    lines = [f"[Workflow {plan.workflow_id} -- {plan.status}]"]
    lines.append(
        render_operation_status_for_user(summary) if summary.operations else "No steps executed."
    )
    unknown = [(s.step_id, s.capability) for s in plan.steps if s.status == "unknown_outcome"]
    if unknown:
        lines.append(
            "⚠ UNKNOWN OUTCOME -- these steps crashed mid-execution and are NOT safely "
            "re-runnable; the side effect may or may not have been applied. Check manually:"
        )
        for step_id, capability in unknown:
            lines.append(f"  - {step_id}: {capability}")
    skipped = [s.step_id for s in plan.steps if s.status == "skipped"]
    if skipped:
        lines.append(f"Skipped (blocked by a failed dependency): {', '.join(skipped)}")
    compensated = [(s.step_id, s.compensation_note) for s in plan.steps if s.status == "compensated"]
    if compensated:
        lines.append("Compensation applied:")
        for step_id, note in compensated:
            lines.append(f"  - {step_id}: {note}")
    if plan.status == "paused_for_approval" and plan.pending_approval_step_id:
        pending = plan.step(plan.pending_approval_step_id)
        if pending is not None and pending.error:
            # e.g. the re-approval note resolve_approval leaves after a
            # stale (restart-invalidated) signature -- the human deserves to
            # know why they are being asked again.
            lines.append(f"Note: {pending.error}")
        lines.append(
            f"Awaiting approval for step {plan.pending_approval_step_id!r} -- "
            f"use `/workflow approve {plan.workflow_id}` / "
            f"`/workflow deny {plan.workflow_id} <reason>` (CLI) or "
            f"POST /workflow/{plan.workflow_id}/resolve (API) to continue."
        )
    return "\n".join(lines)


# ── Engine ────────────────────────────────────────────────────────────────────

class WorkflowEngine:
    """Constructed once per (tools, settings, workspace) -- the same shape
    jarvis.graph.graph.build_graph()'s own node factories already use.
    Stateless between calls; all durable state lives in the WorkflowPlan
    itself and jarvis.execution.workflow_store."""

    def __init__(
        self,
        tools: list,
        settings,
        workspace: Path,
        *,
        transport: str = "workflow",
        conversation_id: str = "",
    ):
        self._tools_by_name = {t.name: t for t in tools}
        self._settings = settings
        self._workspace = workspace
        # Faz 7.3 (P1): execution context for the audit trail. The graph
        # path gets transport from _HudEventCallback's constructor and
        # records every policy decision + risk>=2 execution in
        # data/audit_log.jsonl -- workflow steps previously produced ZERO
        # audit entries (the engine calls tool.ainvoke() directly, so no
        # LangChain callback ever fires). Same event vocabulary as
        # nodes.py/_HudEventCallback, plus workflow_id/step_id fields.
        self._transport = transport
        self._conversation_id = conversation_id

    def _audit(self, event: str, plan: WorkflowPlan, step: WorkflowStep, **fields) -> None:
        audit_log.record(
            event,
            transport=self._transport,
            conversation_id=self._conversation_id,
            workflow_id=plan.workflow_id,
            step_id=step.step_id,
            **fields,
        )

    # ── Plan lifecycle ──────────────────────────────────────────────────────

    def create_plan(
        self,
        task_contract: TaskContract,
        steps: list[WorkflowStep],
        *,
        max_steps: int = 12,
        max_replans: int = 1,
    ) -> WorkflowPlan:
        now = datetime.now(timezone.utc).isoformat()
        plan = WorkflowPlan(
            workflow_id=f"wf-{uuid.uuid4().hex[:12]}",
            task_contract=task_contract,
            steps=steps,
            max_steps=max_steps,
            max_replans=max_replans,
            status="planned",
            created_at=now,
            updated_at=now,
        )
        workflow_store.save(plan)
        return plan

    def replan(self, plan: WorkflowPlan, new_steps: list[WorkflowStep]) -> WorkflowPlan:
        """Append new_steps within the plan's max_replans budget. Does NOT
        decide whether a replan is warranted or what the steps should be --
        see this module's docstring."""
        if plan.replan_count >= plan.max_replans:
            raise ValueError(
                f"workflow {plan.workflow_id} has exhausted its replan budget ({plan.max_replans})"
            )
        existing_ids = {s.step_id for s in plan.steps}
        collisions = [s.step_id for s in new_steps if s.step_id in existing_ids]
        if collisions:
            raise ValueError(f"replan step_id(s) already exist in this plan: {collisions}")
        plan.steps.extend(new_steps)
        plan.replan_count += 1
        if plan.is_terminal():
            plan.status = "running"
        workflow_store.save(plan)
        return plan

    # ── Advance loop ────────────────────────────────────────────────────────

    async def advance(self, plan: WorkflowPlan) -> WorkflowPlan:
        """Run ready steps until the plan pauses for approval, exhausts its
        step budget, deadlocks, or finalizes. Always persists before
        returning. Safe to call repeatedly (e.g. after a resolve_approval())
        -- a terminal or paused plan is returned immediately, unchanged."""
        self._recover_interrupted_steps(plan)

        while True:
            if plan.status == "paused_for_approval":
                return plan
            if plan.is_terminal():
                return plan

            if plan.executed_count() >= plan.max_steps:
                for s in plan.steps:
                    if s.status == "pending":
                        s.status = "skipped"
                        s.error = "step budget exhausted"
                await self._finalize(plan)
                return plan

            ready = plan.ready_steps()
            if not ready:
                unresolved = [s for s in plan.steps if s.status == "pending"]
                if unresolved:
                    for s in unresolved:
                        s.status = "failed"
                        s.error = "unresolvable dependency (missing step id or dependency cycle)"
                await self._finalize(plan)
                return plan

            step = ready[0]
            plan.status = "running"
            await self._run_step(plan, step)
            workflow_store.save(plan)
            if plan.status == "paused_for_approval":
                return plan
            # loop continues -- other independent ready branches may proceed
            # even though this one paused/failed/succeeded.

    async def resolve_approval(self, plan: WorkflowPlan, step_id: str, decision: str) -> WorkflowPlan:
        """decision: "approve" or "deny[:reason]" -- same vocabulary
        confirmation_node's own interrupt/resume already uses. Resolves
        exactly this one step; call advance() again afterward to keep the
        rest of the plan moving (see this module's docstring)."""
        if plan.status != "paused_for_approval" or plan.pending_approval_step_id != step_id:
            raise ValueError(
                f"workflow {plan.workflow_id} is not currently awaiting approval for step {step_id!r}"
            )
        step = plan.step(step_id)
        if step is None or step.status != "needs_approval":
            raise ValueError(f"step {step_id!r} not found or not awaiting approval")

        plan.pending_approval_step_id = None
        plan.status = "running"

        req_fields = step.approval_request or {}
        if decision.lower().startswith("deny"):
            reason = decision[4:].lstrip(":").strip() or "denied by user"
            step.status = "failed"
            step.error = f"denied: {reason}"
            self._audit(
                "decision", plan, step, tool=step.capability,
                action=req_fields.get("action", ""), risk_level=req_fields.get("risk_level", 0),
                outcome="user_denied", reason=reason,
            )
            plan.propagate_skip(step.step_id, f"dependency {step.step_id} was denied ({reason})")
            workflow_store.save(plan)
            return plan

        req = ExecutionRequest(**step.approval_request)
        current_digest = tool_call_fingerprint(step.capability, step.args)
        ok, why = approval.verify(req, step.approval_signature, current_args_digest=current_digest)
        if not ok:
            # Faz 7.3 (P1): a stale binding must NOT kill the workflow -- and
            # must NEVER execute under the old yes. The dominant real cause is
            # a process restart rotating approval.py's process-local HMAC key
            # (its own documented tradeoff), which previously contradicted
            # workflow.py's whole reason for persisting the request/signature.
            # Recovery: send the step back through its own gate from scratch --
            # _run_step re-validates args, re-evaluates policy (the CURRENT
            # policy, which may have changed across the restart), and pauses
            # again with a freshly signed request for the human to re-approve.
            # Every verify failure gets this same path: expiry and key
            # rotation are the expected cases, and a tampered persisted row
            # is also safest re-shown to the human rather than half-trusted.
            step.status = "pending"
            step.execution_id = None
            step.approval_request = None
            step.approval_signature = None
            step.error = (
                f"previous approval was no longer valid ({why}); "
                "a fresh approval request was issued -- please re-approve"
            )
            self._audit(
                "decision", plan, step, tool=step.capability,
                action=req_fields.get("action", ""), risk_level=req_fields.get("risk_level", 0),
                outcome="blocked_stale_approval", reason=why,
            )
            await self._run_step(plan, step)
            workflow_store.save(plan)
            return plan
        approval.consume(req)
        self._audit(
            "decision", plan, step, tool=step.capability,
            action=req_fields.get("action", ""), risk_level=req_fields.get("risk_level", 0),
            outcome="user_approved",
        )

        await self._dispatch(plan, step)
        if step.status == "failed":
            plan.propagate_skip(step.step_id, f"dependency {step.step_id} failed: {step.error}")
        workflow_store.save(plan)
        return plan

    # ── Compensation ────────────────────────────────────────────────────────

    async def compensate(self, plan: WorkflowPlan) -> WorkflowPlan:
        """Walk succeeded steps in reverse, applying a registered
        compensator where one exists. Steps with no compensator are left
        "succeeded" -- honestly uncompensated, never silently claimed
        reverted. Safe to call more than once: an already-"compensated"
        step is skipped."""
        for step in reversed(plan.steps):
            if step.status != "succeeded":
                continue
            entry = _COMPENSATORS.get(_compensator_key(step.capability, step.args))
            if entry is None:
                continue
            _capture_fn, compensate_fn = entry
            content = (step.envelope or {}).get("normalized_output") or ""
            try:
                note = await compensate_fn(step, content, self._workspace, self._tools_by_name)
            except Exception as exc:  # noqa: BLE001 -- compensation must never itself crash
                note = f"compensation attempt raised: {exc}"
            step.compensation_note = note
            step.status = "compensated"
            self._audit(
                "compensation", plan, step, tool=step.capability,
                note=redact_preview(note),
            )
        workflow_store.save(plan)
        return plan

    # ── Reporting ───────────────────────────────────────────────────────────

    def report(self, plan: WorkflowPlan) -> str:
        """Human-facing final summary. Thin wrapper over the module-level
        render_workflow_report() -- kept as a method too since every
        existing caller (tests, and any future graph-side code holding an
        engine instance already) uses it that way."""
        return render_workflow_report(plan)

    # ── Internals ───────────────────────────────────────────────────────────

    def _recover_interrupted_steps(self, plan: WorkflowPlan) -> None:
        """A step left "running" means the process died mid-dispatch (see
        module docstring). A journal hit (idempotency.is_committed) is the
        ground truth for "it definitely landed". The ABSENCE of a journal
        entry is NOT ground truth for "it never happened" -- the crash
        window includes "tool succeeded, process died before commit()".
        So (Faz 7.3, P0) an uncommitted running step is only reset to
        pending when its capability is classified safely re-runnable
        (ToolSpec.idempotency == "natural" -- see tool_registry's
        _IDEMPOTENCY); anything else -- gmail send, calendar create, an
        unclassified MCP tool -- is parked as "unknown_outcome": terminal,
        never auto-retried, reported for manual reconciliation. Its
        dependents are skipped exactly as if it had failed."""
        for step in plan.steps:
            if step.status != "running":
                continue
            if step.execution_id and idempotency.is_committed(step.execution_id):
                step.status = "succeeded"
                step.error = None
                continue
            spec = get_spec(step.capability)
            if spec is not None and spec.idempotency == "natural":
                step.status = "pending"
            else:
                step.status = "unknown_outcome"
                step.error = (
                    "crashed mid-execution; this capability is not safely re-runnable "
                    "(idempotency: "
                    + (spec.idempotency if spec is not None else "unknown capability")
                    + ") -- the side effect may or may not have been applied. "
                    "Verify manually before retrying."
                )
                plan.propagate_skip(
                    step.step_id,
                    f"dependency {step.step_id} has an unknown outcome after a crash",
                )

    async def _run_step(self, plan: WorkflowPlan, step: WorkflowStep) -> None:
        """Validate -> risk-classify -> (pause for approval) -> dispatch.
        Mutates step/plan in place; does not persist (advance()/
        resolve_approval() do, right after calling this)."""
        spec = get_spec(step.capability)

        if spec is not None and spec.args_schema is not None:
            ok, errors = validate_args(spec.args_schema, step.args)
            if not ok:
                first = errors[0] if errors else {"loc": [], "msg": "invalid arguments"}
                step.status = "failed"
                step.error = f"invalid_args {first.get('loc')}: {first.get('msg')}"[:300]
                self._audit(
                    "decision", plan, step, tool=step.capability, action="", risk_level=0,
                    outcome="blocked_invalid_args",
                    reason=f"{first.get('loc')}: {first.get('msg')}"[:200],
                )
                plan.propagate_skip(step.step_id, f"dependency {step.step_id} had invalid arguments")
                return

        decision = policy_guard.evaluate(step.capability, step.args, self._settings)

        if not decision.allowed:
            step.status = "failed"
            step.error = f"blocked ({decision.veto_kind}): {decision.reason}"
            self._audit(
                "decision", plan, step, tool=decision.tool, action=decision.action,
                risk_level=decision.risk_level,
                outcome="blocked_kill_switch" if decision.veto_kind == "kill_switch"
                else "blocked_capability_disabled",
                reason=decision.reason,
            )
            plan.propagate_skip(step.step_id, f"dependency {step.step_id} was blocked ({decision.reason})")
            return

        if (
            not getattr(self._settings, "external_writes_enabled", True)
            and decision.side_effect_type == "external_write"
        ):
            step.status = "failed"
            step.error = "blocked: external writes disabled in this profile"
            self._audit(
                "decision", plan, step, tool=decision.tool, action=decision.action,
                risk_level=decision.risk_level, outcome="blocked_external_writes_disabled",
                reason="EXTERNAL_WRITES_ENABLED=false",
            )
            plan.propagate_skip(
                step.step_id, f"dependency {step.step_id} blocked (external writes disabled)"
            )
            return

        if decision.requires_confirmation and getattr(self._settings, "confirmation_gate_enabled", True):
            self._audit(
                "decision", plan, step, tool=decision.tool, action=decision.action,
                risk_level=decision.risk_level, outcome="confirm_required", reason=decision.reason,
            )
            ttl = getattr(self._settings, "approval_ttl_sec", 300)
            now_iso = datetime.now(timezone.utc).isoformat()
            req = ExecutionRequest(
                execution_id=f"{plan.workflow_id}-{step.step_id}-{secrets.token_hex(6)}",
                capability=step.capability,
                action=decision.action,
                normalized_args_digest=tool_call_fingerprint(step.capability, step.args),
                target_resource=resolve_target_resource(step.capability, step.args),
                risk_level=decision.risk_level,
                requires_confirmation=decision.requires_confirmation,
                allowed=decision.allowed,
                side_effect_type=decision.side_effect_type,
                idempotency=spec.idempotency if spec is not None else "none",
                created_at=now_iso,
                expiry=approval.new_expiry(ttl),
                single_use_nonce=approval.new_nonce(),
            )
            step.status = "needs_approval"
            step.execution_id = req.execution_id
            step.approval_request = req.model_dump()
            step.approval_signature = approval.sign(req)
            plan.status = "paused_for_approval"
            plan.pending_approval_step_id = step.step_id
            return

        if decision.risk_level >= 2:
            self._audit(
                "decision", plan, step, tool=decision.tool, action=decision.action,
                risk_level=decision.risk_level, outcome="auto_approved", reason=decision.reason,
            )
        await self._dispatch(plan, step)
        if step.status == "failed":
            plan.propagate_skip(step.step_id, f"dependency {step.step_id} failed: {step.error}")

    async def _dispatch(self, plan: WorkflowPlan, step: WorkflowStep) -> None:
        """Actually invoke the capability. Assumes validation/policy/
        confirmation already cleared -- called from _run_step's no-
        confirmation-needed path and from resolve_approval's approve path.
        """
        tool = self._tools_by_name.get(step.capability)
        spec = get_spec(step.capability)
        execution_id = step.execution_id or f"{plan.workflow_id}-{step.step_id}-{secrets.token_hex(6)}"
        step.execution_id = execution_id
        step.status = "running"
        workflow_store.save(plan)  # checkpoint BEFORE the call -- see _recover_interrupted_steps

        entry = _COMPENSATORS.get(_compensator_key(step.capability, step.args))
        if entry is not None:
            capture_fn, _compensate_fn = entry
            try:
                step.compensation_data = capture_fn(step.args, self._workspace)
            except Exception:  # noqa: BLE001 -- capture is best-effort only
                step.compensation_data = None

        # Same risk>=2 threshold as _HudEventCallback's on_tool_start (the
        # graph path's execution audit) -- L1 reads are not audited there
        # either. The callback never fires here (direct ainvoke, no
        # LangChain callback manager), so the engine writes the pair itself.
        audit_execution = spec is not None and spec.risk_level >= 2
        if audit_execution:
            self._audit(
                "execution_start", plan, step, tool=step.capability,
                risk_level=spec.risk_level, execution_id=execution_id,
                args_preview=redact_preview(step.args),
            )

        if tool is None:
            content = format_tool_error(step.capability, RuntimeError(f"unknown capability: {step.capability}"))
            ok = False
        else:
            try:
                raw = await asyncio.wait_for(tool.ainvoke(step.args), timeout=_timeout_bound_for(spec))
                content = raw if isinstance(raw, str) else str(raw)
                ok = not content_is_failure(content)
            except Exception as exc:  # noqa: BLE001 -- this boundary is the point, mirrors safe_tools.py
                content = format_tool_error(step.capability, exc)
                ok = False

        if audit_execution:
            self._audit(
                "execution_end", plan, step, tool=step.capability,
                risk_level=spec.risk_level, execution_id=execution_id,
                ok=ok, result_preview=redact_preview(content),
            )

        code = parse_blocked_code(content)
        timed_out, may_still_run, worker_terminated = parse_timeout_flags(content)
        digest = tool_call_fingerprint(step.capability, step.args)
        postcondition_results = (
            run_postconditions(
                spec.postconditions, workspace=self._workspace, args=step.args, tool_result_content=content,
            )
            if spec is not None and spec.postconditions
            else []
        )
        envelope = build_shadow_envelope(
            tool_name=step.capability, args=step.args, ok=ok, content=content,
            retryable="retryable=true" in content, error_code=code,
            execution_id=execution_id, timed_out=timed_out,
            execution_may_still_be_running=may_still_run, worker_terminated=worker_terminated,
            postconditions=postcondition_results,
        )
        step.envelope = envelope.model_dump()
        if ok:
            idempotency.commit(execution_id, step.capability, digest)
            step.status = "succeeded"
            step.error = None
        else:
            step.status = "failed"
            step.error = content[:300]

    async def _finalize(self, plan: WorkflowPlan) -> None:
        statuses = {s.status for s in plan.steps}
        if statuses <= {"succeeded"}:
            # Vacuously true for a zero-step plan too -- nothing failed.
            plan.status = "succeeded"
        elif any(s.status == "succeeded" and self._had_side_effect(s) for s in plan.steps) or (
            # An unknown_outcome step MAY have committed its side effect --
            # "partially_committed" is the honest label; "failed" would
            # falsely promise nothing happened.
            "unknown_outcome" in statuses
        ):
            plan.status = "partially_committed"
        else:
            plan.status = "failed"
        workflow_store.save(plan)
        if plan.status in ("failed", "partially_committed"):
            await self.compensate(plan)

    def _had_side_effect(self, step: WorkflowStep) -> bool:
        spec = get_spec(step.capability)
        if spec is None:
            return True  # unknown capability -- assume the riskier case
        return spec.side_effect_type not in ("none", "local_read", "external_read")
