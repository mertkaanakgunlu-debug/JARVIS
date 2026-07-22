"""Agent Runtime rev.2, Faz 7 -- jarvis.execution.workflow_store's
checkpoint/resume persistence. Uses isolated_cwd (resolves paths.data_dir(),
same rule as idempotency/kill_switch/audit_log -- see MEMORY.md's
isolate-test-data-paths lesson).
"""
from __future__ import annotations

from jarvis.execution import workflow_store
from jarvis.execution.contract import TaskContract
from jarvis.execution.workflow import WorkflowPlan, WorkflowStep


def _plan(workflow_id: str = "wf-1", **overrides) -> WorkflowPlan:
    steps = overrides.pop(
        "steps", [WorkflowStep(step_id="a", capability="file_write", args={"path": "x.txt"})]
    )
    return WorkflowPlan(
        workflow_id=workflow_id,
        task_contract=TaskContract(task_id="t1", user_goal="test goal"),
        steps=steps,
        created_at="2026-07-22T00:00:00+00:00",
        **overrides,
    )


def test_load_missing_workflow_returns_none(isolated_cwd):
    assert workflow_store.load("does-not-exist") is None


def test_save_then_load_roundtrips_a_plan(isolated_cwd):
    plan = _plan()
    workflow_store.save(plan)

    loaded = workflow_store.load("wf-1")

    assert loaded is not None
    assert loaded.workflow_id == "wf-1"
    assert loaded.steps[0].step_id == "a"
    assert loaded.steps[0].args == {"path": "x.txt"}


def test_save_overwrites_existing_row_for_the_same_workflow_id(isolated_cwd):
    plan = _plan(status="planned")
    workflow_store.save(plan)

    plan.status = "succeeded"
    plan.steps[0].status = "succeeded"
    workflow_store.save(plan)

    loaded = workflow_store.load("wf-1")
    assert loaded.status == "succeeded"
    assert loaded.steps[0].status == "succeeded"


def test_list_workflows_orders_newest_updated_first(isolated_cwd):
    workflow_store.save(_plan("wf-a"))
    workflow_store.save(_plan("wf-b"))
    # touch wf-a again so it becomes the most recently updated
    workflow_store.save(_plan("wf-a", status="running"))

    rows = workflow_store.list_workflows()
    assert rows[0]["workflow_id"] == "wf-a"


def test_list_workflows_filters_by_status(isolated_cwd):
    workflow_store.save(_plan("wf-a", status="succeeded"))
    workflow_store.save(_plan("wf-b", status="failed"))

    rows = workflow_store.list_workflows(status="failed")
    assert [r["workflow_id"] for r in rows] == ["wf-b"]


def test_delete_removes_the_row(isolated_cwd):
    workflow_store.save(_plan())
    assert workflow_store.delete("wf-1") is True
    assert workflow_store.load("wf-1") is None


def test_delete_missing_workflow_returns_false(isolated_cwd):
    assert workflow_store.delete("never-existed") is False


def test_a_running_step_with_execution_id_roundtrips(isolated_cwd):
    """The exact shape WorkflowEngine._recover_interrupted_steps() depends
    on after a simulated crash -- see test_workflow_engine.py's recovery
    tests for the behavior this enables."""
    plan = _plan(steps=[
        WorkflowStep(step_id="a", capability="file_write", status="running", execution_id="exec-1"),
    ])
    workflow_store.save(plan)

    loaded = workflow_store.load("wf-1")

    assert loaded.steps[0].status == "running"
    assert loaded.steps[0].execution_id == "exec-1"


def test_a_needs_approval_step_with_its_binding_roundtrips(isolated_cwd):
    """The exact shape resolve_approval() depends on surviving a process
    restart during an approval pause."""
    plan = _plan(
        steps=[
            WorkflowStep(
                step_id="a", capability="shell_run", status="needs_approval",
                approval_request={"execution_id": "exec-2", "capability": "shell_run"},
                approval_signature="deadbeef",
            ),
        ],
        status="paused_for_approval",
        pending_approval_step_id="a",
    )
    workflow_store.save(plan)

    loaded = workflow_store.load("wf-1")

    assert loaded.status == "paused_for_approval"
    assert loaded.pending_approval_step_id == "a"
    assert loaded.steps[0].approval_signature == "deadbeef"
    assert loaded.steps[0].approval_request["execution_id"] == "exec-2"
