"""Faz 7.3 (P0) -- crash recovery must never re-execute a non-idempotent
side effect.

The P0 finding: _dispatch() commits to the idempotency journal only AFTER a
successful tool call, so the crash window "tool succeeded, process died
before commit()" leaves a running step with an empty journal -- and the old
recovery blindly reset that to pending, re-running gmail sends / calendar
creates on the next advance(). These tests pin the corrected policy:

  running + journal hit                        -> succeeded (unchanged)
  running + no journal + idempotency "natural" -> pending, safe retry (unchanged)
  running + no journal + anything else         -> unknown_outcome: terminal,
      never re-executed, dependents skipped, reported for manual check.

The "true fault injection" test the review demanded is here too: a real
dispatch where the (fake external) tool genuinely succeeds and
idempotency.commit is made to raise -- the exact commit-window crash --
then recovery runs against what workflow_store actually persisted.

Fake tool objects are used for gmail deliberately, unlike
test_workflow_engine.py's real-make_tools() approach: a real gmail tool
would hit the network, and the entire point here is controlling the
success/crash boundary precisely. get_spec("gmail") still resolves the REAL
registry spec, so the classification consulted is production data, not a
fixture.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.config import Settings
from jarvis.execution import idempotency, workflow_store
from jarvis.execution.contract import TaskContract
from jarvis.execution.workflow import WorkflowStep
from jarvis.execution.workflow_engine import WorkflowEngine, render_workflow_report
from jarvis.tool_registry import TOOL_SPECS, get_spec


class _FakeTool:
    """Minimal stand-in with the two attributes WorkflowEngine uses
    (.name, .ainvoke) -- counts invocations so a test can assert a call
    was NOT re-executed."""

    def __init__(self, name: str, result: str = "ok -- done"):
        self.name = name
        self.result = result
        self.calls: list[dict] = []

    async def ainvoke(self, args: dict) -> str:
        self.calls.append(args)
        return self.result


def _contract() -> TaskContract:
    return TaskContract(task_id="t-crash", user_goal="crash recovery test")


def _gmail_send_args() -> dict:
    return {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}


# ── Registry classification invariants ───────────────────────────────────────

def test_all_external_l3_write_tools_are_classified_not_retryable():
    for name in ("gmail", "google_calendar", "google_drive", "itu_mail"):
        assert get_spec(name).idempotency == "none", name


def test_no_static_tool_is_classified_keyed_until_a_key_is_actually_wired():
    # "keyed" means the dispatch layer passes execution_id as a service-side
    # idempotency key. No tool implementation accepts one today -- if this
    # fails, someone flipped the classification without wiring the key
    # (see _IDEMPOTENCY's comment in tool_registry.py).
    assert not [s.name for s in TOOL_SPECS.values() if s.idempotency == "keyed"]


def test_file_write_is_naturally_idempotent():
    assert get_spec("file_write").idempotency == "natural"


# ── Recovery policy ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_uncommitted_running_external_write_parks_as_unknown_outcome(isolated_cwd, tmp_path):
    gmail = _FakeTool("gmail")
    settings = Settings(_env_file=None, confirmation_gate_enabled=False)
    engine = WorkflowEngine([gmail], settings, tmp_path)
    steps = [
        WorkflowStep(
            step_id="s1", capability="gmail", args=_gmail_send_args(),
            status="running", execution_id="exec-crashed-send",
        ),
        WorkflowStep(step_id="s2", capability="gmail", args=_gmail_send_args(), dependencies=["s1"]),
    ]
    plan = engine.create_plan(_contract(), steps)
    # journal deliberately empty: the crash happened somewhere between
    # dispatch and commit -- nobody knows whether the send landed.

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "unknown_outcome"
    assert gmail.calls == []  # NEVER re-executed -- the entire P0
    assert plan.step("s2").status == "skipped"
    assert plan.status == "partially_committed"  # may have landed -- not "failed"
    assert "may or may not" in plan.step("s1").error


@pytest.mark.asyncio
async def test_committed_running_step_still_recovers_as_succeeded(isolated_cwd, tmp_path):
    gmail = _FakeTool("gmail")
    settings = Settings(_env_file=None, confirmation_gate_enabled=False)
    engine = WorkflowEngine([gmail], settings, tmp_path)
    step = WorkflowStep(
        step_id="s1", capability="gmail", args=_gmail_send_args(),
        status="running", execution_id="exec-landed-send",
    )
    plan = engine.create_plan(_contract(), [step])
    idempotency.commit("exec-landed-send", "gmail", "digest")

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "succeeded"
    assert gmail.calls == []  # journal says it already ran -- no second send


@pytest.mark.asyncio
async def test_unknown_capability_parks_rather_than_retries(isolated_cwd, tmp_path):
    # A dynamically-registered (MCP) tool whose spec is gone after restart:
    # get_spec() returns None -- fail closed, same as idempotency "none".
    settings = Settings(_env_file=None, confirmation_gate_enabled=False)
    engine = WorkflowEngine([], settings, tmp_path)
    step = WorkflowStep(
        step_id="s1", capability="browser_click", args={},
        status="running", execution_id="exec-mcp-crash",
    )
    plan = engine.create_plan(_contract(), [step])

    plan = await engine.advance(plan)

    assert plan.step("s1").status == "unknown_outcome"


@pytest.mark.asyncio
async def test_report_surfaces_unknown_outcome_for_manual_reconciliation(isolated_cwd, tmp_path):
    gmail = _FakeTool("gmail")
    settings = Settings(_env_file=None, confirmation_gate_enabled=False)
    engine = WorkflowEngine([gmail], settings, tmp_path)
    step = WorkflowStep(
        step_id="s1", capability="gmail", args=_gmail_send_args(),
        status="running", execution_id="exec-report-check",
    )
    plan = engine.create_plan(_contract(), [step])

    plan = await engine.advance(plan)
    text = render_workflow_report(plan)

    assert "UNKNOWN OUTCOME" in text
    assert "Check manually" in text
    assert "s1: gmail" in text


# ── True fault injection: tool succeeds, crash right before commit ───────────

@pytest.mark.asyncio
async def test_crash_between_tool_success_and_journal_commit_is_not_reexecuted(
    isolated_cwd, tmp_path
):
    """The review's exact demanded scenario, end to end: dispatch a real
    step whose tool RETURNS SUCCESS, crash (raise) at idempotency.commit,
    then recover from what workflow_store actually persisted.

    patch.object as a context manager, NOT the test's monkeypatch fixture:
    a manual monkeypatch.undo() would also undo isolated_cwd's chdir (same
    fixture instance), silently pointing the rest of the test at the REAL
    project data dir -- the exact incident class MEMORY.md's isolate-test-
    data-paths lesson records."""
    gmail = _FakeTool("gmail", result="Email sent to a@b.c.")
    settings = Settings(_env_file=None, confirmation_gate_enabled=False)
    engine = WorkflowEngine([gmail], settings, tmp_path)
    step = WorkflowStep(step_id="s1", capability="gmail", args=_gmail_send_args())
    plan = engine.create_plan(_contract(), [step])

    crash = RuntimeError("simulated process death before journal commit")
    with patch.object(idempotency, "commit", side_effect=crash):
        with pytest.raises(RuntimeError, match="simulated process death"):
            await engine.advance(plan)

    assert gmail.calls, "precondition: the tool really did run before the crash"
    persisted = workflow_store.load(plan.workflow_id)
    assert persisted.step("s1").status == "running"  # what a restart actually sees

    recovered = await engine.advance(persisted)

    assert recovered.step("s1").status == "unknown_outcome"
    assert len(gmail.calls) == 1  # the pre-crash call -- and NO second send
    assert recovered.status == "partially_committed"


@pytest.mark.asyncio
async def test_same_commit_crash_for_natural_capability_recovers_by_rerunning(
    isolated_cwd, tmp_path
):
    """Contrast case: the identical commit-window crash on file_write
    (classified "natural") IS safely re-run -- re-writing the same content
    converges, so parking it would be needless manual work."""
    writer = _FakeTool("file_write", result="written")
    settings = Settings(_env_file=None, confirmation_gate_enabled=False)
    engine = WorkflowEngine([writer], settings, tmp_path)
    step = WorkflowStep(step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"})
    plan = engine.create_plan(_contract(), [step])

    crash = RuntimeError("simulated process death before journal commit")
    with patch.object(idempotency, "commit", side_effect=crash):
        with pytest.raises(RuntimeError):
            await engine.advance(plan)

    persisted = workflow_store.load(plan.workflow_id)
    recovered = await engine.advance(persisted)

    assert recovered.step("s1").status == "succeeded"
    assert len(writer.calls) == 2  # original + the safe retry
    assert recovered.status == "succeeded"
