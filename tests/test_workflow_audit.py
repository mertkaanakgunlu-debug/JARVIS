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


def test_workflow_start_schema_does_not_expose_config(isolated_cwd, tmp_path):
    from unittest.mock import MagicMock

    from jarvis.graph import tools as graph_tools

    settings = Settings(_env_file=None)
    tools = graph_tools.make_tools(tmp_path, settings, MagicMock())
    wf_start = next(t for t in tools if t.name == "workflow_start")
    schema_props = wf_start.tool_call_schema.model_json_schema().get("properties", {})
    assert set(schema_props) == {"goal", "steps"}  # config injected, never model-facing
