"""Agent Runtime rev.2, Faz 7 -- jarvis.execution.workflow's pure (no I/O)
readiness/propagation/terminal-status logic. See jarvis.execution.workflow's
own module docstring for the honest scope limits (static args, no data-flow
between steps) this module's shapes assume.
"""
from __future__ import annotations

from jarvis.execution.contract import TaskContract
from jarvis.execution.workflow import WorkflowPlan, WorkflowStep


def _contract(**overrides) -> TaskContract:
    return TaskContract(task_id="t1", user_goal="test goal", **overrides)


def _plan(steps: list[WorkflowStep], **overrides) -> WorkflowPlan:
    return WorkflowPlan(workflow_id="wf-test", task_contract=_contract(), steps=steps, **overrides)


# ── ready_steps() / is_ready() ───────────────────────────────────────────────

def test_step_with_no_dependencies_is_ready_when_pending():
    plan = _plan([WorkflowStep(step_id="a", capability="file_write")])
    assert [s.step_id for s in plan.ready_steps()] == ["a"]


def test_step_is_not_ready_until_its_dependency_succeeds():
    plan = _plan([
        WorkflowStep(step_id="a", capability="file_write"),
        WorkflowStep(step_id="b", capability="file_write", dependencies=["a"]),
    ])
    assert [s.step_id for s in plan.ready_steps()] == ["a"]

    plan.step("a").status = "succeeded"
    assert [s.step_id for s in plan.ready_steps()] == ["b"]


def test_is_ready_false_for_a_non_pending_step():
    step = WorkflowStep(step_id="a", capability="file_write", status="succeeded")
    plan = _plan([step])
    assert plan.is_ready(step) is False


def test_dangling_dependency_id_is_never_ready():
    """A dependency that doesn't match any step in the plan is a
    plan-construction bug -- treated as permanently unsatisfiable, not
    silently ignored (WorkflowEngine.advance()'s deadlock check is what
    turns this into an explicit failure)."""
    step = WorkflowStep(step_id="a", capability="file_write", dependencies=["ghost"])
    plan = _plan([step])
    assert plan.is_ready(step) is False


def test_multiple_independent_steps_are_all_ready_together():
    plan = _plan([
        WorkflowStep(step_id="a", capability="file_write"),
        WorkflowStep(step_id="b", capability="file_write"),
    ])
    assert {s.step_id for s in plan.ready_steps()} == {"a", "b"}


# ── has_blocked_dependency() ─────────────────────────────────────────────────

def test_has_blocked_dependency_true_when_dependency_failed():
    plan = _plan([
        WorkflowStep(step_id="a", capability="file_write", status="failed"),
        WorkflowStep(step_id="b", capability="file_write", dependencies=["a"]),
    ])
    assert plan.has_blocked_dependency(plan.step("b")) is True


def test_has_blocked_dependency_false_when_dependency_succeeded():
    plan = _plan([
        WorkflowStep(step_id="a", capability="file_write", status="succeeded"),
        WorkflowStep(step_id="b", capability="file_write", dependencies=["a"]),
    ])
    assert plan.has_blocked_dependency(plan.step("b")) is False


# ── propagate_skip() ──────────────────────────────────────────────────────────

def test_propagate_skip_cascades_transitively():
    plan = _plan([
        WorkflowStep(step_id="a", capability="file_write", status="failed"),
        WorkflowStep(step_id="b", capability="file_write", dependencies=["a"]),
        WorkflowStep(step_id="c", capability="file_write", dependencies=["b"]),
    ])
    skipped = plan.propagate_skip("a", "a failed")
    assert set(skipped) == {"b", "c"}
    assert plan.step("b").status == "skipped"
    assert plan.step("c").status == "skipped"
    assert plan.step("c").error == "a failed"


def test_propagate_skip_does_not_touch_unrelated_steps():
    plan = _plan([
        WorkflowStep(step_id="a", capability="file_write", status="failed"),
        WorkflowStep(step_id="b", capability="file_write", dependencies=["a"]),
        WorkflowStep(step_id="z", capability="file_write"),  # unrelated, independent
    ])
    plan.propagate_skip("a", "a failed")
    assert plan.step("z").status == "pending"


def test_propagate_skip_also_cancels_a_step_awaiting_approval():
    plan = _plan([
        WorkflowStep(step_id="a", capability="file_write", status="failed"),
        WorkflowStep(step_id="b", capability="gmail", dependencies=["a"], status="needs_approval"),
    ])
    plan.propagate_skip("a", "a failed")
    assert plan.step("b").status == "skipped"


def test_propagate_skip_is_a_noop_on_an_already_resolved_step():
    plan = _plan([
        WorkflowStep(step_id="a", capability="file_write", status="failed"),
        WorkflowStep(step_id="b", capability="file_write", dependencies=["a"], status="succeeded"),
    ])
    skipped = plan.propagate_skip("a", "a failed")
    assert skipped == []
    assert plan.step("b").status == "succeeded"


# ── executed_count() / is_terminal() / all_resolved() ────────────────────────

def test_executed_count_counts_every_non_pending_step():
    plan = _plan([
        WorkflowStep(step_id="a", capability="file_write", status="succeeded"),
        WorkflowStep(step_id="b", capability="file_write", status="failed"),
        WorkflowStep(step_id="c", capability="file_write", status="pending"),
    ])
    assert plan.executed_count() == 2


def test_is_terminal_true_only_for_succeeded_failed_or_partially_committed():
    for status in ("succeeded", "failed", "partially_committed"):
        assert _plan([], status=status).is_terminal() is True
    for status in ("planned", "running", "paused_for_approval"):
        assert _plan([], status=status).is_terminal() is False


def test_all_resolved_false_while_a_step_is_pending_or_awaiting_approval():
    assert _plan([WorkflowStep(step_id="a", capability="file_write")]).all_resolved() is False
    assert _plan([
        WorkflowStep(step_id="a", capability="gmail", status="needs_approval"),
    ]).all_resolved() is False


def test_all_resolved_true_once_every_step_has_a_final_status():
    plan = _plan([
        WorkflowStep(step_id="a", capability="file_write", status="succeeded"),
        WorkflowStep(step_id="b", capability="file_write", status="skipped"),
    ])
    assert plan.all_resolved() is True


def test_step_lookup_returns_none_for_an_unknown_id():
    plan = _plan([WorkflowStep(step_id="a", capability="file_write")])
    assert plan.step("nope") is None
