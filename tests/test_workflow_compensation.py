"""Faz 7.3 (P1) -- a failed compensation attempt must never be reported as
a successful one.

Before this, compensate() set step.status = "compensated" unconditionally,
regardless of what the compensator's returned string actually said ("failed
to restore ...", "cannot compensate ..." all still became "compensated"),
and render_workflow_report() headlined that section "Compensation applied"
even when the note said the opposite. Two consequences fixed here:
  1. A user reading the report could believe a rollback happened when it
     didn't (e.g., a stray created file was never actually deleted).
  2. Because compensate() only reconsiders status=="succeeded" steps, a
     step that failed to compensate could never be retried -- it was
     permanently stuck reporting a false "compensated".

These tests use the REAL file_write compensator (jarvis.execution.
workflow_engine._compensate_file_write) against a real filesystem, not a
fake -- the whole defect was in how the ENGINE interprets that function's
result, which a fake compensator would paper over.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis import audit_log
from jarvis.config import Settings
from jarvis.execution.contract import TaskContract
from jarvis.execution.workflow import WorkflowStep
from jarvis.execution.workflow_engine import WorkflowEngine, render_workflow_report


def _contract() -> TaskContract:
    return TaskContract(task_id="t-comp", user_goal="compensation test")


def _engine(workspace, **overrides) -> WorkflowEngine:
    return WorkflowEngine([], Settings(_env_file=None, **overrides), workspace, transport="cli")


# ── Failure is reported honestly, not as "compensated" ───────────────────────

@pytest.mark.asyncio
async def test_missing_capture_data_fails_compensation_not_silently_succeeds(isolated_cwd, tmp_path):
    step = WorkflowStep(
        step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"},
        status="succeeded", compensation_data=None,  # capture never ran / found nothing
    )
    engine = _engine(tmp_path)
    plan = engine.create_plan(_contract(), [step])

    plan = await engine.compensate(plan)

    assert plan.step("s1").status == "compensation_failed"
    assert plan.step("s1").status != "compensated"
    assert "cannot compensate" in plan.step("s1").compensation_note


@pytest.mark.asyncio
async def test_report_never_headlines_a_failed_compensation_as_applied(isolated_cwd, tmp_path):
    step = WorkflowStep(
        step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"},
        status="succeeded", compensation_data=None,
    )
    engine = _engine(tmp_path)
    plan = engine.create_plan(_contract(), [step])

    plan = await engine.compensate(plan)
    text = render_workflow_report(plan)

    assert "COMPENSATION FAILED" in text
    assert "Check manually" in text
    assert "Compensation applied" not in text  # the exact false-positive headline this fixes


@pytest.mark.asyncio
async def test_real_filesystem_restore_failure_is_marked_compensation_failed(isolated_cwd, tmp_path):
    # write_text against a path that is actually a directory always raises
    # an OSError subclass on both POSIX (IsADirectoryError) and Windows
    # (PermissionError) -- a real, not simulated, restore failure.
    blocking_dir = tmp_path / "blocked"
    blocking_dir.mkdir()
    step = WorkflowStep(
        step_id="s1", capability="file_write", args={"path": "blocked", "content": "new"},
        status="succeeded",
        compensation_data={"path": str(blocking_dir), "existed": True, "previous_content": "original"},
    )
    engine = _engine(tmp_path)
    plan = engine.create_plan(_contract(), [step])

    plan = await engine.compensate(plan)

    assert plan.step("s1").status == "compensation_failed"
    assert "failed to restore" in plan.step("s1").compensation_note


# ── Audit trail carries the real ok/retryable outcome ────────────────────────

@pytest.mark.asyncio
async def test_compensation_audit_event_carries_ok_false_on_failure(isolated_cwd, tmp_path):
    step = WorkflowStep(
        step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"},
        status="succeeded", compensation_data=None,
    )
    engine = _engine(tmp_path)
    plan = engine.create_plan(_contract(), [step])

    plan = await engine.compensate(plan)

    events = [e for e in audit_log.tail(20) if e.get("workflow_id") == plan.workflow_id]
    comp = [e for e in events if e["event"] == "compensation"]
    assert comp and comp[-1]["ok"] is False
    assert comp[-1]["retryable"] is False  # missing capture data will never resolve itself


@pytest.mark.asyncio
async def test_compensation_audit_event_carries_ok_true_on_success(isolated_cwd, tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("original")
    step = WorkflowStep(
        step_id="s1", capability="file_write", args={"path": "existing.txt", "content": "new"},
        status="succeeded",
        compensation_data={"path": str(target), "existed": True, "previous_content": "original"},
    )
    engine = _engine(tmp_path)
    plan = engine.create_plan(_contract(), [step])

    plan = await engine.compensate(plan)

    assert plan.step("s1").status == "compensated"
    events = [e for e in audit_log.tail(20) if e.get("workflow_id") == plan.workflow_id]
    comp = [e for e in events if e["event"] == "compensation"]
    assert comp and comp[-1]["ok"] is True


# ── A failed compensation is retriable on a later call ────────────────────────

@pytest.mark.asyncio
async def test_failed_compensation_is_retried_and_can_succeed_on_a_second_call(
    isolated_cwd, tmp_path, monkeypatch
):
    target = tmp_path / "existing.txt"
    target.write_text("new")  # simulates: file_write already overwrote the original content
    step = WorkflowStep(
        step_id="s1", capability="file_write", args={"path": "existing.txt", "content": "new"},
        status="succeeded",
        compensation_data={"path": str(target), "existed": True, "previous_content": "original"},
    )
    engine = _engine(tmp_path)
    plan = engine.create_plan(_contract(), [step])

    real_write_text = Path.write_text
    call_count = {"n": 0}

    def _flaky_write_text(self, *a, **k):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise OSError("simulated transient failure on first attempt")
        return real_write_text(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", _flaky_write_text)

    plan = await engine.compensate(plan)
    assert plan.step("s1").status == "compensation_failed"
    assert target.read_text() == "new"  # not yet restored -- the attempt genuinely failed

    plan = await engine.compensate(plan)  # retry: NOT skipped, unlike the old "succeeded"-only filter
    assert plan.step("s1").status == "compensated"
    assert target.read_text() == "original"
    assert call_count["n"] == 2


@pytest.mark.asyncio
async def test_an_already_compensated_step_is_not_reprocessed(isolated_cwd, tmp_path):
    target = tmp_path / "existing.txt"
    target.write_text("original")
    step = WorkflowStep(
        step_id="s1", capability="file_write", args={"path": "existing.txt", "content": "new"},
        status="succeeded",
        compensation_data={"path": str(target), "existed": True, "previous_content": "original"},
    )
    engine = _engine(tmp_path)
    plan = engine.create_plan(_contract(), [step])

    plan = await engine.compensate(plan)
    assert plan.step("s1").status == "compensated"
    note_after_first = plan.step("s1").compensation_note

    target.write_text("mutated after compensation")  # simulate something else touching the file
    plan = await engine.compensate(plan)  # must be a no-op: already "compensated", not "succeeded"

    assert plan.step("s1").compensation_note == note_after_first
    assert target.read_text() == "mutated after compensation"  # untouched by the second call
