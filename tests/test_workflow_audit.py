"""Faz 7.3 (P1) -- workflow steps must not bypass the audit core.

Before this, WorkflowEngine called tool.ainvoke() directly: no LangChain
callback ever fired, so a workflow's risky steps produced ZERO entries in
data/audit_log.jsonl -- breaking Faz 4's "one security and audit kernel"
contract. These tests pin that the engine now writes the SAME event
vocabulary as the graph path (nodes.py's decision events,
_HudEventCallback's execution_start/end pair), extended with
workflow_id/step_id/transport/conversation_id fields.

Also pins the config plumbing: workflow_start's RunnableConfig parameter
must never leak into the model-facing schema (a local model would try to
fill it), and the transport it reads must land in the audit rows.
"""
from __future__ import annotations

import pytest

from jarvis import audit_log
from jarvis.config import Settings
from jarvis.execution.contract import TaskContract
from jarvis.execution.workflow import WorkflowStep
from jarvis.execution.workflow_engine import WorkflowEngine


class _FakeTool:
    def __init__(self, name: str, result: str = "ok -- done"):
        self.name = name
        self.result = result
        self.calls: list[dict] = []

    async def ainvoke(self, args: dict) -> str:
        self.calls.append(args)
        return self.result


def _contract() -> TaskContract:
    return TaskContract(task_id="t-audit", user_goal="audit test")


def _events_for(workflow_id: str) -> list[dict]:
    return [e for e in audit_log.tail(50) if e.get("workflow_id") == workflow_id]


@pytest.mark.asyncio
async def test_risk2_dispatch_writes_decision_and_execution_pair(isolated_cwd, tmp_path):
    writer = _FakeTool("file_write", result="written")
    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    engine = WorkflowEngine(
        [writer], settings, tmp_path, transport="cli", conversation_id="conv-1"
    )
    plan = engine.create_plan(
        _contract(),
        [WorkflowStep(step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"})],
    )
    plan = await engine.advance(plan)
    assert plan.status == "succeeded"

    mine = _events_for(plan.workflow_id)
    decisions = [e for e in mine if e["event"] == "decision"]
    assert decisions and decisions[0]["outcome"] == "auto_approved"
    assert decisions[0]["tool"] == "file_write"
    assert decisions[0]["transport"] == "cli"
    assert decisions[0]["conversation_id"] == "conv-1"
    assert decisions[0]["step_id"] == "s1"

    starts = [e for e in mine if e["event"] == "execution_start"]
    ends = [e for e in mine if e["event"] == "execution_end"]
    assert len(starts) == 1 and len(ends) == 1
    assert starts[0]["execution_id"] == ends[0]["execution_id"]
    assert ends[0]["ok"] is True
    # Redacted previews, not raw args/content -- same redaction layer as
    # the graph path's callback.
    assert "args_preview" in starts[0] and "result_preview" in ends[0]


@pytest.mark.asyncio
async def test_confirm_pause_writes_confirm_required_decision(isolated_cwd, tmp_path):
    gmail = _FakeTool("gmail")
    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    engine = WorkflowEngine([gmail], settings, tmp_path, transport="api")
    plan = engine.create_plan(
        _contract(),
        [WorkflowStep(
            step_id="s1", capability="gmail",
            args={"action": "send", "to": "a@b.c", "subject": "s", "body": "b"},
        )],
    )
    plan = await engine.advance(plan)
    assert plan.status == "paused_for_approval"

    mine = _events_for(plan.workflow_id)
    assert [e["outcome"] for e in mine if e["event"] == "decision"] == ["confirm_required"]
    assert mine[0]["risk_level"] == 3
    # paused, not executed -- so no execution events yet
    assert not [e for e in mine if e["event"].startswith("execution")]


@pytest.mark.asyncio
async def test_invalid_args_and_blocked_capability_are_audited(isolated_cwd, tmp_path):
    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    engine = WorkflowEngine([], settings, tmp_path, transport="cli")
    plan = engine.create_plan(
        _contract(),
        [
            # gmail send with a missing body -> blocked_invalid_args
            WorkflowStep(step_id="s1", capability="gmail", args={"action": "send", "to": "a@b.c"}),
            # python_run is alpha-disabled -> policy veto
            WorkflowStep(step_id="s2", capability="python_run", args={"path": "x.py"}),
        ],
    )
    plan = await engine.advance(plan)

    mine = _events_for(plan.workflow_id)
    outcomes = {e["step_id"]: e["outcome"] for e in mine if e["event"] == "decision"}
    assert outcomes["s1"] == "blocked_invalid_args"
    assert outcomes["s2"] == "blocked_capability_disabled"


@pytest.mark.asyncio
async def test_capability_disabled_veto_writes_policy_decision_trace(isolated_cwd, tmp_path, monkeypatch):
    """Review remediation: a pre-execution policy veto in the engine never
    dispatches a tool, so on_tool_start/end (and tool_trace's own execution
    rows) never fire for it -- before this fix, the engine wrote only the
    audit_log "decision" row, never the tool_trace "policy_decision" row
    nodes.py's identical graph-path veto writes, leaving the eval/oracle
    harness with no structural evidence a workflow-path block happened.
    Mirrors test_confirmation_node.py's test_kill_switch_veto_writes_policy_decision_trace
    for the graph path."""
    from jarvis import tool_trace

    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")
    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    engine = WorkflowEngine([], settings, tmp_path, transport="cli")
    plan = engine.create_plan(
        _contract(),
        [WorkflowStep(step_id="s1", capability="python_run", args={"path": "x.py"})],
    )
    plan = await engine.advance(plan)

    rows = [r for r in tool_trace.load() if r.get("event") == "policy_decision"]
    assert rows, "expected a policy_decision trace row for the capability-disabled veto"
    assert rows[-1]["outcome"] == "blocked_capability_disabled"
    assert rows[-1]["ok"] is False
    assert rows[-1]["workflow_id"] == plan.workflow_id
    assert rows[-1]["step_id"] == "s1"


@pytest.mark.asyncio
async def test_external_writes_disabled_veto_writes_policy_decision_trace(isolated_cwd, tmp_path, monkeypatch):
    from jarvis import tool_trace

    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")
    settings = Settings(_env_file=None, confirmation_gate_enabled=True, external_writes_enabled=False)
    engine = WorkflowEngine([], settings, tmp_path, transport="cli")
    plan = engine.create_plan(
        _contract(),
        [WorkflowStep(
            step_id="s1", capability="gmail",
            args={"action": "send", "to": "a@b.c", "subject": "s", "body": "b"},
        )],
    )
    plan = await engine.advance(plan)

    rows = [r for r in tool_trace.load() if r.get("event") == "policy_decision"]
    assert rows, "expected a policy_decision trace row for the external-writes-disabled veto"
    assert rows[-1]["outcome"] == "blocked_external_writes_disabled"
    assert rows[-1]["ok"] is False


@pytest.mark.asyncio
async def test_failed_dispatch_audits_execution_end_not_ok(isolated_cwd, tmp_path):
    failer = _FakeTool("file_write", result="[ERROR] disk full")
    settings = Settings(_env_file=None, confirmation_gate_enabled=True)
    engine = WorkflowEngine([failer], settings, tmp_path, transport="cli")
    plan = engine.create_plan(
        _contract(),
        [WorkflowStep(step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"})],
    )
    plan = await engine.advance(plan)
    assert plan.step("s1").status == "failed"

    ends = [e for e in _events_for(plan.workflow_id) if e["event"] == "execution_end"]
    assert ends and ends[0]["ok"] is False


@pytest.mark.asyncio
async def test_compensation_outcome_is_audited(isolated_cwd, tmp_path):
    writer = _FakeTool("file_write", result="written")
    failer = _FakeTool("gmail", result="[ERROR] smtp down")
    settings = Settings(_env_file=None, confirmation_gate_enabled=False)
    engine = WorkflowEngine([writer, failer], settings, tmp_path, transport="cli")
    plan = engine.create_plan(
        _contract(),
        [
            WorkflowStep(step_id="s1", capability="file_write", args={"path": "a.txt", "content": "x"}),
            WorkflowStep(
                step_id="s2", capability="gmail",
                args={"action": "send", "to": "a@b.c", "subject": "s", "body": "b"},
                dependencies=["s1"],
            ),
        ],
    )
    plan = await engine.advance(plan)
    assert plan.step("s1").status == "compensated"

    comp = [e for e in _events_for(plan.workflow_id) if e["event"] == "compensation"]
    assert comp and comp[0]["step_id"] == "s1" and comp[0]["tool"] == "file_write"


@pytest.mark.asyncio
async def test_todo_compensation_writes_execution_audit_pair(isolated_cwd, tmp_path):
    """Review remediation: unlike _compensate_file_write (direct filesystem
    I/O, nothing to audit at the tool-execution granularity),
    _compensate_todo_add calls a real registered tool's ainvoke() directly
    -- before this fix, that call bypassed the audit core entirely, so the
    compensating delete produced only the coarse "compensation" event, never
    the execution_start/execution_end pair every other risk>=2 tool call
    gets."""
    todo = _FakeTool("todo", result="eklendi [abc123]")
    failer = _FakeTool("gmail", result="[ERROR] smtp down")
    settings = Settings(_env_file=None, confirmation_gate_enabled=False)
    engine = WorkflowEngine([todo, failer], settings, tmp_path, transport="cli")
    plan = engine.create_plan(
        _contract(),
        [
            WorkflowStep(step_id="s1", capability="todo", args={"action": "add", "title": "buy milk"}),
            WorkflowStep(
                step_id="s2", capability="gmail",
                args={"action": "send", "to": "a@b.c", "subject": "s", "body": "b"},
                dependencies=["s1"],
            ),
        ],
    )
    plan = await engine.advance(plan)
    assert plan.step("s1").status == "compensated"
    # The compensating delete actually ran against the real todo tool.
    assert todo.calls[-1] == {"action": "delete", "todo_id": "abc123"}

    mine = _events_for(plan.workflow_id)
    # Two execution_start/end pairs for "todo" now exist: the original add
    # dispatch (from _dispatch()'s own pre-existing audit) and the NEW
    # compensating delete this fix adds -- distinguish by execution_id, which
    # _compensate_todo_add stamps with a "compensate-" prefix.
    comp_starts = [
        e for e in mine
        if e["event"] == "execution_start" and e["tool"] == "todo"
        and e["execution_id"].startswith("compensate-")
    ]
    comp_ends = [
        e for e in mine
        if e["event"] == "execution_end" and e["tool"] == "todo"
        and e["execution_id"].startswith("compensate-")
    ]
    assert len(comp_starts) == 1 and len(comp_ends) == 1
    assert comp_starts[0]["execution_id"] == comp_ends[0]["execution_id"]
    assert comp_ends[0]["ok"] is True
    assert comp_starts[0]["step_id"] == "s1"


def test_workflow_start_schema_does_not_expose_config(isolated_cwd, tmp_path):
    from unittest.mock import MagicMock

    from jarvis.graph import tools as graph_tools

    settings = Settings(_env_file=None)
    tools = graph_tools.make_tools(tmp_path, settings, MagicMock())
    wf_start = next(t for t in tools if t.name == "workflow_start")
    schema_props = wf_start.tool_call_schema.model_json_schema().get("properties", {})
    assert set(schema_props) == {"goal", "steps"}  # config injected, never model-facing
