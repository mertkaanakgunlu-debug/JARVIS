"""Agent Runtime rev.2, Faz 7 Part 2 -- the workflow_start/workflow_status
@tool wrappers (jarvis/graph/tools.py), live-wiring WorkflowEngine to the
model-facing tool surface. Uses REAL tools from make_tools() (same
precedent as test_workflow_engine.py/test_langchain_dispatch_coercion.py).

make_tools()'s `memory` parameter is a MagicMock here, not a real
jarvis.memory.Memory -- see test_workflow_engine.py's module docstring for
why (none of the capabilities exercised in this file ever touch it, and a
real one's ChromaDB construction repeated across every test here was
CI-only load this suite doesn't need to pay).
"""
from __future__ import annotations

import json
import re
from unittest.mock import MagicMock

import pytest

from jarvis.config import Settings
from jarvis.graph import tools as graph_tools


def _tools(workspace, **settings_overrides):
    settings = Settings(_env_file=None, confirmation_gate_enabled=True, **settings_overrides)
    memory = MagicMock()
    return {t.name: t for t in graph_tools.make_tools(workspace, settings, memory)}


def _workflow_id(report: str) -> str:
    m = re.search(r"\[Workflow (wf-[0-9a-f]+)", report)
    assert m, f"no workflow id found in report:\n{report}"
    return m.group(1)


@pytest.mark.asyncio
async def test_workflow_start_runs_a_two_step_plan_and_reports_success(isolated_cwd, tmp_path):
    tools = _tools(tmp_path)
    steps = json.dumps([
        {"step_id": "s1", "capability": "file_write", "args": {"path": "a.txt", "content": "hi"}},
        {"step_id": "s2", "capability": "file_write", "args": {"path": "b.txt", "content": "yo"},
         "dependencies": ["s1"]},
    ])

    report = await tools["workflow_start"].ainvoke({"goal": "write two files", "steps": steps})

    assert "succeeded" in report
    assert (tmp_path / "a.txt").read_text() == "hi"
    assert (tmp_path / "b.txt").read_text() == "yo"


@pytest.mark.asyncio
async def test_workflow_start_rejects_invalid_json(isolated_cwd, tmp_path):
    tools = _tools(tmp_path)
    report = await tools["workflow_start"].ainvoke({"goal": "x", "steps": "not json"})
    assert report.startswith("⚠")


@pytest.mark.asyncio
async def test_workflow_start_rejects_an_unknown_capability(isolated_cwd, tmp_path):
    tools = _tools(tmp_path)
    steps = json.dumps([{"step_id": "s1", "capability": "not_a_real_tool", "args": {}}])
    report = await tools["workflow_start"].ainvoke({"goal": "x", "steps": steps})
    assert "⚠" in report
    assert "not_a_real_tool" in report


@pytest.mark.asyncio
async def test_workflow_start_rejects_a_disabled_alpha_capability(isolated_cwd, tmp_path):
    """python_run is alpha-disabled -- structurally absent from make_tools(),
    so it must be rejected here exactly like any other unknown capability,
    never silently accepted into a workflow step."""
    tools = _tools(tmp_path)
    steps = json.dumps([{"step_id": "s1", "capability": "python_run", "args": {"code": "1+1"}}])
    report = await tools["workflow_start"].ainvoke({"goal": "x", "steps": steps})
    assert "⚠" in report
    assert "python_run" in report


@pytest.mark.asyncio
async def test_workflow_start_rejects_a_duplicate_step_id(isolated_cwd, tmp_path):
    tools = _tools(tmp_path)
    steps = json.dumps([
        {"step_id": "s1", "capability": "file_write", "args": {"path": "a.txt", "content": "x"}},
        {"step_id": "s1", "capability": "file_write", "args": {"path": "b.txt", "content": "y"}},
    ])
    report = await tools["workflow_start"].ainvoke({"goal": "x", "steps": steps})
    assert "⚠" in report
    assert "Tekrarlanan" in report


@pytest.mark.asyncio
async def test_workflow_start_rejects_an_unknown_dependency(isolated_cwd, tmp_path):
    tools = _tools(tmp_path)
    steps = json.dumps([
        {"step_id": "s1", "capability": "file_write", "args": {"path": "a.txt", "content": "x"},
         "dependencies": ["ghost"]},
    ])
    report = await tools["workflow_start"].ainvoke({"goal": "x", "steps": steps})
    assert "⚠" in report
    assert "ghost" in report


@pytest.mark.asyncio
async def test_workflow_start_pauses_and_report_names_the_cli_approval_command(isolated_cwd, tmp_path):
    tools = _tools(tmp_path)
    steps = json.dumps([{"step_id": "s1", "capability": "shell_run", "args": {"command": "echo hi"}}])

    report = await tools["workflow_start"].ainvoke({"goal": "run a command", "steps": steps})

    assert "paused_for_approval" in report
    assert "/workflow approve" in report
    assert "/workflow deny" in report


@pytest.mark.asyncio
async def test_workflow_status_reports_a_previously_started_workflow(isolated_cwd, tmp_path):
    tools = _tools(tmp_path)
    steps = json.dumps([{"step_id": "s1", "capability": "file_write", "args": {"path": "a.txt", "content": "x"}}])
    start_report = await tools["workflow_start"].ainvoke({"goal": "x", "steps": steps})
    wf_id = _workflow_id(start_report)

    status_report = await tools["workflow_status"].ainvoke({"workflow_id": wf_id})

    assert wf_id in status_report
    assert "succeeded" in status_report


@pytest.mark.asyncio
async def test_workflow_status_reports_an_unknown_id_honestly(isolated_cwd, tmp_path):
    tools = _tools(tmp_path)
    report = await tools["workflow_status"].ainvoke({"workflow_id": "wf-does-not-exist"})
    assert "⚠" in report
    assert "bulunamadı" in report
