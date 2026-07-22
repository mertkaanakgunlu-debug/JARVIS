"""Faz 7.3 -- the review's three medium-severity findings.

1. render_workflow_report() previously only covered steps that reached
   dispatch (had a real envelope) -- a step that failed validation or a
   policy veto BEFORE dispatch was invisible in the report entirely; a
   dependent's "blocked by a failed dependency" line named the blocker's
   step_id but never said WHY it failed. Fixed by _step_table(): every
   step, unconditionally.
2. WorkflowEngine.resolve_approval() only ever checked for a "deny"
   prefix -- "yes", "", a typo all silently fell through to approve.
   Fixed with the same exact allowlist workflow_approval.py's service
   layer already enforces, now ALSO checked at the engine itself
   (defense in depth -- the engine must stay safe to call directly).
3. replan() only compared new_steps against the plan's EXISTING ids --
   two steps sharing an id WITHIN the same new_steps batch both passed,
   and WorkflowPlan.step() silently resolves to the first one.
"""
from __future__ import annotations

import pytest

from jarvis.config import Settings
from jarvis.execution.contract import TaskContract
from jarvis.execution.workflow import WorkflowStep
from jarvis.execution.workflow_engine import WorkflowEngine, render_workflow_report


class _FakeTool:
    def __init__(self, name: str, result: str = "ok -- done"):
        self.name = name
        self.result = result
        self.calls: list[dict] = []

    async def ainvoke(self, args: dict) -> str:
        self.calls.append(args)
        return self.result


def _contract() -> TaskContract:
    return TaskContract(task_id="t-medium", user_goal="medium findings test")


def _engine(tools, workspace, **overrides) -> WorkflowEngine:
    return WorkflowEngine(tools, Settings(_env_file=None, **overrides), workspace, transport="cli")


# ── 1. Step table surfaces pre-dispatch failure reasons ───────────────────────

@pytest.mark.asyncio
async def test_report_surfaces_invalid_args_reason_previously_invisible(isolated_cwd, tmp_path):
    engine = _engine([], tmp_path, confirmation_gate_enabled=True)
    plan = engine.create_plan(
        _contract(),
        [
            # gmail send missing 'body' -> invalid_args, never dispatched, no envelope
            WorkflowStep(step_id="s1", capability="gmail", args={"action": "send", "to": "a@b.c", "subject": "s"}),
            WorkflowStep(step_id="s2", capability="gmail", args={"action": "send", "to": "a@b.c"}, dependencies=["s1"]),
        ],
    )
    plan = await engine.advance(plan)
    assert plan.step("s1").status == "failed"
    assert "invalid_args" in plan.step("s1").error

    text = render_workflow_report(plan)

    # The old bug: the report only ever said "Skipped (blocked by a failed
    # dependency): s2" -- s1's OWN failure reason never appeared anywhere.
    s1_row = next(line for line in text.splitlines() if line.strip().startswith("s1 |"))
    assert "failed" in s1_row and "invalid_args" in s1_row
    s2_row = next(line for line in text.splitlines() if line.strip().startswith("s2 |"))
    assert "skipped" in s2_row


@pytest.mark.asyncio
async def test_report_surfaces_policy_veto_reason_for_disabled_capability(isolated_cwd, tmp_path):
    engine = _engine([], tmp_path, confirmation_gate_enabled=True)
    plan = engine.create_plan(
        _contract(), [WorkflowStep(step_id="s1", capability="python_run", args={"path": "x.py"})]
    )
    plan = await engine.advance(plan)

    text = render_workflow_report(plan)

    assert "s1 | python_run | failed | blocked (capability_disabled)" in text


@pytest.mark.asyncio
async def test_step_table_lists_every_step_including_succeeded_ones(isolated_cwd, tmp_path):
    writer = _FakeTool("file_write", result="written")
    engine = _engine([writer], tmp_path, confirmation_gate_enabled=True)
    plan = engine.create_plan(
        _contract(),
        [WorkflowStep(step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"})],
    )
    plan = await engine.advance(plan)

    text = render_workflow_report(plan)
    header = "Steps (step_id | capability | status | error | execution_id | compensation):"
    assert header in text
    row = [line for line in text.splitlines() if line.strip().startswith("s1 |")][0]
    assert "file_write" in row and "succeeded" in row
    assert plan.step("s1").execution_id in row


# ── 2. resolve_approval() exact decision allowlist (engine-level, defense in depth) ──

@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["yes", "", "invalid", "approve please", "APPROVED", "ok"])
async def test_resolve_approval_rejects_anything_not_exactly_approve_or_deny(isolated_cwd, tmp_path, bad):
    gmail = _FakeTool("gmail")
    engine = _engine([gmail], tmp_path, confirmation_gate_enabled=True)
    plan = engine.create_plan(
        _contract(),
        [WorkflowStep(
            step_id="s1", capability="gmail",
            args={"action": "send", "to": "a@b.c", "subject": "s", "body": "b"},
        )],
    )
    plan = await engine.advance(plan)
    assert plan.status == "paused_for_approval"

    with pytest.raises(ValueError, match="invalid decision"):
        await engine.resolve_approval(plan, "s1", bad)

    # Nothing executed, and the plan is still cleanly awaiting a real decision.
    assert gmail.calls == []
    assert plan.status == "paused_for_approval"
    assert plan.step("s1").status == "needs_approval"


@pytest.mark.asyncio
async def test_resolve_approval_still_accepts_the_real_vocabulary(isolated_cwd, tmp_path):
    gmail = _FakeTool("gmail", result="sent")
    engine = _engine([gmail], tmp_path, confirmation_gate_enabled=True)
    plan = engine.create_plan(
        _contract(),
        [WorkflowStep(
            step_id="s1", capability="gmail",
            args={"action": "send", "to": "a@b.c", "subject": "s", "body": "b"},
        )],
    )
    plan = await engine.advance(plan)

    plan = await engine.resolve_approval(plan, "s1", "APPROVE")  # case-insensitive verb
    assert plan.step("s1").status == "succeeded"
    assert len(gmail.calls) == 1


# ── 3. replan() catches duplicate ids within the SAME new_steps batch ────────

@pytest.mark.asyncio
async def test_replan_rejects_duplicate_step_ids_within_new_steps_itself(isolated_cwd, tmp_path):
    engine = _engine([], tmp_path)
    plan = engine.create_plan(_contract(), [WorkflowStep(step_id="s1", capability="file_write", args={})])

    dupes = [
        WorkflowStep(step_id="s2", capability="file_write", args={"path": "a.txt", "content": "1"}),
        WorkflowStep(step_id="s2", capability="file_write", args={"path": "b.txt", "content": "2"}),
    ]
    with pytest.raises(ValueError, match="duplicate step_id"):
        engine.replan(plan, dupes)

    # Rejected atomically -- neither zombie step was appended.
    assert plan.step("s2") is None
    assert len(plan.steps) == 1
    assert plan.replan_count == 0


@pytest.mark.asyncio
async def test_replan_still_accepts_genuinely_unique_new_steps(isolated_cwd, tmp_path):
    engine = _engine([], tmp_path)
    plan = engine.create_plan(_contract(), [WorkflowStep(step_id="s1", capability="file_write", args={})])

    plan = engine.replan(plan, [
        WorkflowStep(step_id="s2", capability="file_write", args={"path": "a.txt", "content": "1"}),
        WorkflowStep(step_id="s3", capability="file_write", args={"path": "b.txt", "content": "2"}),
    ])

    assert plan.step("s2") is not None and plan.step("s3") is not None
    assert plan.replan_count == 1
