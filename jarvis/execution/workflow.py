"""WorkflowStep / WorkflowPlan -- Agent Runtime rev.2, Faz 7 (Workflow runtime).

The plan's own framing (reviewer #7): `max_tool_rounds_per_turn` (today: 2)
is a single-turn budget, not built for long, multi-step daily tasks. This
module defines the shapes for a workflow that is explicitly SEPARATE from
the single-turn chat graph (jarvis/graph/graph.py) -- it reuses Faz 1-4's
execution contract (ExecutionRequest/ExecutionEnvelope/postconditions/
VerifiedExecutionSummary) for each step's actual dispatch rather than
building a second, parallel verification vocabulary (plan principle #2 --
"dogrulama sozlugu tek kaynak").

This module is pure shapes + pure (no I/O) helper logic, mirroring
jarvis.execution.contract/postcondition's own "Faz 1 only defines the shape"
precedent. jarvis.execution.workflow_store does persistence;
jarvis.execution.workflow_engine does the actual step dispatch, the
advance/pause/resume loop, and compensation -- kept separate so this module
never needs to import jarvis.tool_registry/policy_guard/the tool objects
themselves, and so its own logic (readiness, propagation, terminal-status
computation) is trivially unit-testable with no mocking.

Honest scope limits, stated up front rather than discovered later:
  - A step's `args` are a static dict fixed when the step is created. There
    is no data-flow/templating mechanism for "step 2's args reference step
    1's output" -- the plan's own WorkflowStep sketch names no such
    mechanism, and building one would be a real, separate design (expression
    language, output schema per capability) with no named consumer yet.
    Whatever builds a WorkflowPlan is responsible for resolving any
    cross-step references before minting it.
  - `max_replans`/`replan_count` are a real, enforced BUDGET (see
    WorkflowEngine.replan) -- but nothing in this phase decides *when* a
    replan is needed or *what* the new steps should be (that needs an LLM
    call this phase does not add, mirroring Faz 1's shadow ledger existing
    before Faz 2 first made a decision from it). The mechanism is real and
    tested; the trigger is future work.
  - "Workflow-level final validation" is the same VerifiedExecutionSummary
    aggregation Faz 4 already built (all_confirmed/any_failed/any_unverified),
    applied to the whole plan's collected step envelopes instead of one
    turn's -- not a new TaskContract-matching validator, since nothing in
    this repo produces a populated TaskContract yet (contract.py's own
    honesty note still applies).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from jarvis.execution.contract import TaskContract

StepStatus = Literal[
    "pending", "running", "succeeded", "failed", "skipped", "needs_approval", "compensated",
    # Faz 7.3 (P0): the process died mid-dispatch and the capability is not
    # safely re-runnable (ToolSpec.idempotency != "natural") -- the side
    # effect MAY or MAY NOT have landed, and nothing here can find out.
    # Terminal for the step; never auto-retried; surfaced in the report as
    # "check manually". One status, not two ("needs_manual_reconciliation"
    # would carry identical information with an extra transition to define).
    "unknown_outcome",
    # Faz 7.3 (P1): compensate() ATTEMPTED to reverse this step's side
    # effect and the attempt itself failed (see workflow_engine.py's
    # CompensationResult) -- distinct from "compensated" (the rollback
    # genuinely happened) so a report can never claim a failed rollback
    # succeeded. Distinct from "succeeded" so a second compensate() call
    # can tell "never attempted" from "attempted and failed" -- retrying a
    # step already sitting at "succeeded" would be a no-op skip, not a
    # retry, if this status were folded back into "succeeded" instead.
    "compensation_failed",
]

# Statuses a step can be "stuck" in that block it from ever running --
# propagate_skip() walks dependents of a step in any of these.
# NOT "compensated"/"compensation_failed" here, deliberately matching that
# existing omission: both only ever get set inside compensate(), which only
# ever runs from _finalize() after the plan is already terminal -- no
# dependent step is evaluated against readiness/blocking again afterward.
_BLOCKING_STATUSES = frozenset({"failed", "skipped", "unknown_outcome"})

WorkflowStatus = Literal[
    "planned", "running", "paused_for_approval", "succeeded", "failed", "partially_committed",
]


class WorkflowStep(BaseModel):
    """One capability call within a WorkflowPlan.

    `envelope` holds the step's own ExecutionEnvelope (.model_dump()'d) once
    it has actually run -- this is what lets WorkflowEngine.report() reuse
    jarvis.execution.summary's existing renderers unchanged (a list of
    envelopes is exactly what build_verified_summary() already consumes).

    `compensation_data`/`compensation_note`: see workflow_engine.py's
    compensator registry. `compensation_data` is captured BEFORE dispatch
    (e.g. a file's prior content) by a registered capturer, if one exists
    for this (capability, action) pair -- None means either no compensator
    is registered, or the registered one has nothing to capture (a brand
    new file). `compensation_note` is filled in only if compensate() later
    actually ran for this step (human-readable outcome, never silently
    assumed).
    """

    step_id: str
    capability: str
    args: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    status: StepStatus = "pending"
    execution_id: str | None = None
    error: str | None = None
    envelope: dict[str, Any] | None = None
    compensation_data: dict[str, Any] | None = None
    compensation_note: str | None = None

    # Set only while status == "needs_approval" -- the signed ExecutionRequest
    # (.model_dump()'d) and its HMAC signature (jarvis.execution.approval),
    # persisted so a process restart during an approval pause doesn't lose
    # the binding resolve_approval() later re-verifies against.
    approval_request: dict[str, Any] | None = None
    approval_signature: str | None = None


class WorkflowPlan(BaseModel):
    workflow_id: str
    task_contract: TaskContract
    steps: list[WorkflowStep] = Field(default_factory=list)
    max_steps: int = 12
    max_replans: int = 1
    replan_count: int = 0
    status: WorkflowStatus = "planned"
    pending_approval_step_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def step(self, step_id: str) -> WorkflowStep | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def is_ready(self, step: WorkflowStep) -> bool:
        """All declared dependencies exist AND already succeeded.

        A dependency id that doesn't match any step in this plan is treated
        as unsatisfiable (never ready) rather than ignored -- a dangling
        reference is a plan-construction bug, not something to silently
        route around; WorkflowEngine.advance()'s deadlock check surfaces it
        as an explicit failure instead of hanging.
        """
        if step.status != "pending":
            return False
        for dep_id in step.dependencies:
            dep = self.step(dep_id)
            if dep is None or dep.status != "succeeded":
                return False
        return True

    def has_blocked_dependency(self, step: WorkflowStep) -> bool:
        """True if any declared dependency can never succeed -- this step
        must be skipped rather than waited on forever."""
        for dep_id in step.dependencies:
            dep = self.step(dep_id)
            if dep is None or dep.status in _BLOCKING_STATUSES:
                return True
        return False

    def ready_steps(self) -> list[WorkflowStep]:
        return [s for s in self.steps if self.is_ready(s)]

    def propagate_skip(self, from_step_id: str, reason: str) -> list[str]:
        """BFS over dependents of from_step_id, skipping every still-pending
        (or needs_approval) step transitively blocked by it. Returns the
        step_ids actually skipped by this call (for logging/reporting) --
        idempotent, a step already resolved is left untouched."""
        skipped: list[str] = []
        frontier = [from_step_id]
        while frontier:
            current = frontier.pop()
            for s in self.steps:
                if current in s.dependencies and s.status in ("pending", "needs_approval"):
                    s.status = "skipped"
                    s.error = reason
                    skipped.append(s.step_id)
                    frontier.append(s.step_id)
        return skipped

    def executed_count(self) -> int:
        """Steps actually DISPATCHED at least once -- the quantity max_steps
        bounds. Deliberately excludes "skipped": a step cascaded from a
        failed/denied dependency (WorkflowPlan.propagate_skip) was never
        itself dispatched, so it must not silently consume the step budget
        and starve an unrelated, independent branch that never got a
        chance to run."""
        return sum(
            1 for s in self.steps
            if s.status in (
                "succeeded", "failed", "needs_approval", "running", "compensated",
                "unknown_outcome", "compensation_failed",
            )
        )

    def is_terminal(self) -> bool:
        return self.status in ("succeeded", "failed", "partially_committed")

    def all_resolved(self) -> bool:
        """No step is still pending or awaiting approval -- the plan has
        nothing left to advance (though it may not be finalized yet)."""
        return all(s.status not in ("pending", "needs_approval", "running") for s in self.steps)
