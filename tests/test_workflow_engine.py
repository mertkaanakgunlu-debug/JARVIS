"""Agent Runtime rev.2, Faz 7 -- jarvis.execution.workflow_engine.WorkflowEngine.

Uses REAL tool objects from jarvis.graph.tools.make_tools() (same precedent
as test_langchain_dispatch_coercion.py / test_alpha_capabilities.py), not
fakes -- the whole point of this phase is reusing the exact same dispatch/
validation/postcondition/idempotency machinery the single-turn graph uses,
so a test double would hide exactly the integration risk worth covering.
isolated_cwd is required everywhere: workflow_store, idempotency, and the
todo/file tools this file exercises all resolve cwd/JARVIS_HOME-relative
paths (see MEMORY.md's isolate-test-data-paths lesson).
"""
from __future__ import annotations

import pytest

from jarvis.config import Settings
from jarvis.execution import idempotency
from jarvis.execution.contract import TaskContract
from jarvis.execution.workflow import WorkflowStep
from jarvis.execution.workflow_engine import WorkflowEngine
from jarvis.graph import tools as graph_tools
from jarvis.memory import Memory


def _engine(workspace, **settings_overrides) -> WorkflowEngine:
    settings = Settings(_env_file=None, confirmation_gate_enabled=True, **settings_overrides)
    memory = Memory(settings)
    tools = graph_tools.make_tools(workspace, settings, memory)
    return WorkflowEngine(tools, settings, workspace)


def _contract(**overrides) -> TaskContract:
    return TaskContract(task_id="t1", user_goal="test goal", **overrides)


# ── Happy path / dependency ordering ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_two_step_linear_workflow_runs_in_dependency_order(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="file_write", args={"path": "a.txt", "content": "hello"}),
        WorkflowStep(
            step_id="s2", capability="file_write", args={"path": "b.txt", "content": "world"},
            dependencies=["s1"],
        ),
    ]
    plan = engine.create_plan(_contract(), steps)

    plan = await engine.advance(plan)

    assert plan.status == "succeeded"
    assert plan.step("s1").status == "succeeded"
    assert plan.step("s2").status == "succeeded"
    assert (tmp_path / "a.txt").read_text() == "hello"
    assert (tmp_path / "b.txt").read_text() == "world"


@pytest.mark.asyncio
async def test_independent_steps_both_run_with_no_dependency_between_them(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="file_write", args={"path": "a.txt", "content": "1"}),
        WorkflowStep(step_id="s2", capability="file_write", args={"path": "b.txt", "content": "2"}),
    ]
    plan = engine.create_plan(_contract(), steps)

    plan = await engine.advance(plan)

    assert plan.status == "succeeded"
    assert {s.status for s in plan.steps} == {"succeeded"}


# ── Failure propagation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_invalid_args_step_fails_immediately_and_skips_dependents(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [
        # missing subject/body -- gmail's args_schema rejects this (same
        # fixture shape test_bounded_repair.py already validated is invalid)
        WorkflowStep(step_id="s1", capability="gmail", args={"action": "send", "to": "a@b.c"}),
        WorkflowStep(
            step_id="s2", capability="file_write", args={"path": "b.txt", "content": "x"},
            dependencies=["s1"],
        ),
    ]
    plan = engine.create_plan(_contract(), steps)

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "failed"
    assert "invalid_args" in plan.step("s1").error
    assert plan.step("s2").status == "skipped"
    assert plan.status == "failed"


@pytest.mark.asyncio
async def test_a_genuinely_failed_step_propagates_skip_to_its_dependent(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="file_read", args={"path": "does-not-exist.txt"}),
        WorkflowStep(
            step_id="s2", capability="file_write", args={"path": "b.txt", "content": "x"},
            dependencies=["s1"],
        ),
    ]
    plan = engine.create_plan(_contract(), steps)

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "failed"
    assert plan.step("s2").status == "skipped"
    assert plan.status == "failed"


@pytest.mark.asyncio
async def test_dangling_dependency_fails_that_step_instead_of_hanging(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(
            step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"},
            dependencies=["ghost"],
        ),
    ]
    plan = engine.create_plan(_contract(), steps)

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "failed"
    assert "unresolvable dependency" in plan.step("s1").error
    assert plan.status == "failed"


# ── Approval pause / resume ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_step_requiring_confirmation_pauses_the_workflow(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [WorkflowStep(step_id="s1", capability="shell_run", args={"command": "echo hello"})]
    plan = engine.create_plan(_contract(), steps)

    plan = await engine.advance(plan)

    assert plan.status == "paused_for_approval"
    assert plan.pending_approval_step_id == "s1"
    assert plan.step("s1").status == "needs_approval"
    assert plan.step("s1").approval_signature is not None


@pytest.mark.asyncio
async def test_approving_a_paused_step_dispatches_it_and_the_workflow_completes(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [WorkflowStep(step_id="s1", capability="shell_run", args={"command": "echo hello"})]
    plan = engine.create_plan(_contract(), steps)
    plan = await engine.advance(plan)

    plan = await engine.resolve_approval(plan, "s1", "approve")
    plan = await engine.advance(plan)

    assert plan.step("s1").status == "succeeded"
    assert plan.status == "succeeded"


@pytest.mark.asyncio
async def test_denying_a_paused_step_fails_it_and_skips_dependents(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="shell_run", args={"command": "echo hello"}),
        WorkflowStep(
            step_id="s2", capability="file_write", args={"path": "b.txt", "content": "x"},
            dependencies=["s1"],
        ),
    ]
    plan = engine.create_plan(_contract(), steps)
    plan = await engine.advance(plan)

    plan = await engine.resolve_approval(plan, "s1", "deny:not needed")
    plan = await engine.advance(plan)

    assert plan.step("s1").status == "failed"
    assert "not needed" in plan.step("s1").error
    assert plan.step("s2").status == "skipped"
    assert plan.status == "failed"


@pytest.mark.asyncio
async def test_resolve_approval_rejects_the_wrong_step_id(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [WorkflowStep(step_id="s1", capability="shell_run", args={"command": "echo hi"})]
    plan = engine.create_plan(_contract(), steps)
    plan = await engine.advance(plan)

    with pytest.raises(ValueError):
        await engine.resolve_approval(plan, "not-the-paused-step", "approve")


# ── Step budget ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_step_budget_exhaustion_skips_remaining_pending_steps(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"}),
        WorkflowStep(step_id="s2", capability="file_write", args={"path": "b.txt", "content": "y"}),
    ]
    plan = engine.create_plan(_contract(), steps, max_steps=1)

    plan = await engine.advance(plan)

    assert plan.executed_count() == 1
    skipped = [s for s in plan.steps if s.status == "skipped"]
    assert len(skipped) == 1
    assert skipped[0].error == "step budget exhausted"
    # the one step that DID run had a real side effect -> auto-compensated
    # away rather than left standing in an incomplete workflow (see
    # compensation tests below for the mechanism itself).
    assert plan.status == "partially_committed"
    compensated = [s for s in plan.steps if s.status == "compensated"]
    assert len(compensated) == 1


@pytest.mark.asyncio
async def test_a_propagated_skip_does_not_consume_the_step_budget(isolated_cwd, tmp_path):
    """s2 cascades to "skipped" from s1's failure without ever being
    dispatched -- it must not count against max_steps and starve s3, an
    unrelated, independent step that deserves its own real chance to run."""
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="file_read", args={"path": "does-not-exist.txt"}),
        WorkflowStep(
            step_id="s2", capability="file_write", args={"path": "b.txt", "content": "x"},
            dependencies=["s1"],
        ),
        WorkflowStep(step_id="s3", capability="file_write", args={"path": "c.txt", "content": "y"}),
    ]
    plan = engine.create_plan(_contract(), steps, max_steps=2)

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "failed"
    assert plan.step("s2").status == "skipped"
    # s3 actually got dispatched and ran -- not starved by s2's propagated
    # skip silently counting against the budget. It may end up "compensated"
    # rather than "succeeded" since the workflow overall didn't fully
    # succeed (see the compensation tests for that mechanism specifically);
    # the point of THIS test is only that it was never itself skipped for
    # "step budget exhausted".
    assert plan.step("s3").status in ("succeeded", "compensated")
    assert plan.step("s3").error != "step budget exhausted"


# ── Compensation ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_file_write_compensation_restores_previous_content_on_a_later_failure(isolated_cwd, tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("original")
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="file_write", args={"path": "existing.txt", "content": "overwritten"}),
        WorkflowStep(step_id="s2", capability="file_read", args={"path": "does-not-exist.txt"}),
    ]
    plan = engine.create_plan(_contract(), steps)

    plan = await engine.advance(plan)

    assert plan.status == "partially_committed"
    assert plan.step("s1").status == "compensated"
    assert "restored previous content" in plan.step("s1").compensation_note
    assert target.read_text() == "original"


@pytest.mark.asyncio
async def test_file_write_compensation_deletes_a_newly_created_file(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="file_write", args={"path": "brand_new.txt", "content": "hi"}),
        WorkflowStep(step_id="s2", capability="file_read", args={"path": "does-not-exist.txt"}),
    ]
    plan = engine.create_plan(_contract(), steps)

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "compensated"
    assert "deleted newly-created file" in plan.step("s1").compensation_note
    assert not (tmp_path / "brand_new.txt").exists()


@pytest.mark.asyncio
async def test_todo_add_compensation_deletes_the_created_todo(isolated_cwd, tmp_path):
    from jarvis import paths
    from jarvis.todo_store import TodoStore

    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="todo", args={"action": "add", "title": "test task"}),
        WorkflowStep(step_id="s2", capability="file_read", args={"path": "does-not-exist.txt"}),
    ]
    plan = engine.create_plan(_contract(), steps)

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "compensated"
    assert "deleted to-do" in plan.step("s1").compensation_note
    store = TodoStore(paths.data_dir() / "sessions.db")
    assert store.list_open() == []


@pytest.mark.asyncio
async def test_a_capability_with_no_registered_compensator_stays_succeeded(isolated_cwd, tmp_path):
    (tmp_path / "readable.txt").write_text("content")
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="file_read", args={"path": "readable.txt"}),
        WorkflowStep(step_id="s2", capability="file_read", args={"path": "does-not-exist.txt"}),
    ]
    plan = engine.create_plan(_contract(), steps)

    plan = await engine.advance(plan)

    assert plan.status == "failed"  # read-only success carries no side effect to preserve
    assert plan.step("s1").status == "succeeded"  # never touched -- no compensator registered
    assert plan.step("s1").compensation_note is None


# ── Reporting ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_report_includes_step_statuses_and_compensation_notes(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    steps = [
        WorkflowStep(step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"}),
        WorkflowStep(step_id="s2", capability="file_read", args={"path": "does-not-exist.txt"}),
    ]
    plan = engine.create_plan(_contract(), steps)
    plan = await engine.advance(plan)

    text = engine.report(plan)

    assert "partially_committed" in text
    assert "Compensation applied" in text
    assert "s1" in text


# ── Crash recovery ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_recovery_marks_a_committed_running_step_succeeded_without_fabricating_an_envelope(
    isolated_cwd, tmp_path
):
    engine = _engine(tmp_path)
    step = WorkflowStep(
        step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"},
        status="running", execution_id="exec-crash-1",
    )
    plan = engine.create_plan(_contract(), [step])
    idempotency.commit("exec-crash-1", "file_write", "digest")  # simulate: it landed before the crash

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "succeeded"
    assert plan.step("s1").envelope is None  # honestly absent, never fabricated
    assert plan.status == "succeeded"


@pytest.mark.asyncio
async def test_recovery_resets_an_uncommitted_running_step_to_pending_and_retries(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    step = WorkflowStep(
        step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"},
        status="running", execution_id="exec-crash-2",
    )
    plan = engine.create_plan(_contract(), [step])
    # deliberately never committed -- the crash happened before the side
    # effect landed, so a fresh attempt is safe.

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "succeeded"
    assert plan.step("s1").envelope is not None  # this time it actually ran
    assert (tmp_path / "a.txt").read_text() == "x"


# ── Replanning budget ─────────────────────────────────────────────────────────

def test_replan_appends_steps_within_budget(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    plan = engine.create_plan(
        _contract(), [WorkflowStep(step_id="s1", capability="file_write", args={})], max_replans=1,
    )

    plan = engine.replan(plan, [WorkflowStep(step_id="s2", capability="file_write", args={})])

    assert plan.replan_count == 1
    assert {s.step_id for s in plan.steps} == {"s1", "s2"}


def test_replan_raises_once_budget_is_exhausted(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    plan = engine.create_plan(_contract(), [], max_replans=0)

    with pytest.raises(ValueError):
        engine.replan(plan, [WorkflowStep(step_id="s2", capability="file_write", args={})])


def test_replan_rejects_a_duplicate_step_id(isolated_cwd, tmp_path):
    engine = _engine(tmp_path)
    plan = engine.create_plan(_contract(), [WorkflowStep(step_id="s1", capability="file_write", args={})])

    with pytest.raises(ValueError):
        engine.replan(plan, [WorkflowStep(step_id="s1", capability="file_write", args={})])
