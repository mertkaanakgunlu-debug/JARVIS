"""Agent Runtime rev.2, Faz 3 -- timeout_class classification + real enforcement.

Three tiers:
  1. tool_registry.py's _TIMEOUT_CLASSES -- every tool has a real, non-default
     classification (ToolSpec.timeout_class defaulted to "cooperative_async"
     for everything before this phase, which was honestly wrong for most
     tools).
  2. safe_tools.py's format_tool_error() -- the two new honest fields
     (execution_may_still_be_running, worker_terminated), varying correctly
     by timeout_class and by the concrete exception type.
  3. Real enforcement: an actual asyncio.wait_for cancellation through a
     compiled graph's ToolNode, and real subprocess.TimeoutExpired from
     shell.py/python_exec.py with a short, deterministic timeout.
"""
from __future__ import annotations

import subprocess
import time

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, START, END, MessagesState

from jarvis.graph.safe_tools import format_tool_error, make_safe_tool_node
from jarvis.tool_registry import ToolSpec, get_spec
from jarvis.tools import python_exec
from jarvis.tools import shell as shell_tools


# ── Tier 1: _TIMEOUT_CLASSES completeness + spot checks ─────────────────────

_VALID_CLASSES = {
    "cooperative_async", "soft_thread_timeout",
    "hard_process_timeout", "external_request_timeout",
}


def test_every_registered_tool_has_a_valid_timeout_class():
    from jarvis.tool_registry import TOOL_SPECS
    for name, spec in TOOL_SPECS.items():
        assert spec.timeout_class in _VALID_CLASSES, name


@pytest.mark.parametrize("name,expected", [
    ("shell_run", "hard_process_timeout"),
    ("python_run", "hard_process_timeout"),
    ("report_compile", "hard_process_timeout"),
    ("math_solve", "cooperative_async"),
    ("research", "cooperative_async"),
    ("gmail", "external_request_timeout"),
    ("web_search", "external_request_timeout"),
    ("file_read", "soft_thread_timeout"),
    ("file_write", "soft_thread_timeout"),
    ("plot_data", "soft_thread_timeout"),
])
def test_spot_check_timeout_classifications(name, expected):
    assert get_spec(name).timeout_class == expected


# ── Tier 2: format_tool_error()'s honest timeout fields ────────────────────

def test_cooperative_async_timeout_reports_not_still_running():
    text = format_tool_error("research", TimeoutError("request timed out"))
    assert "execution_may_still_be_running=false" in text
    assert "worker_terminated=false" in text


@pytest.mark.parametrize("name", ["file_read", "web_search", "shell_run"])
def test_non_cooperative_timeout_reports_may_still_be_running(name):
    text = format_tool_error(name, TimeoutError("request timed out"))
    assert "execution_may_still_be_running=true" in text


def test_subprocess_timeout_expired_reports_worker_terminated():
    exc = subprocess.TimeoutExpired(cmd="powershell -Command sleep", timeout=1)
    text = format_tool_error("shell_run", exc)
    assert "category=timeout" in text
    assert "execution_may_still_be_running=true" in text
    assert "worker_terminated=true" in text


def test_bare_timeout_error_never_claims_worker_terminated_even_for_hard_process_tool():
    """worker_terminated is only ever true for subprocess.TimeoutExpired
    specifically -- a generic TimeoutError (e.g. the outer wait_for's own
    backstop firing) does NOT get to claim a process was actually killed,
    even for a hard_process_timeout tool."""
    text = format_tool_error("shell_run", TimeoutError("outer bound exceeded"))
    assert "worker_terminated=false" in text


def test_unregistered_tool_timeout_defaults_to_conservative_still_running():
    text = format_tool_error("totally_unknown_tool_xyz", TimeoutError("timed out"))
    assert "execution_may_still_be_running=true" in text


def test_non_timeout_error_unaffected_by_faz3_fields():
    """Regression guard: a non-timeout category must NOT gain the two new
    fields at all -- they are timeout-specific, not universal."""
    text = format_tool_error("gmail", RuntimeError("invalid credential"))
    assert "execution_may_still_be_running" not in text
    assert "worker_terminated" not in text


# ── Tier 3a: real asyncio.wait_for cancellation through the compiled graph ──

def _tool_graph(tools: list):
    builder = StateGraph(MessagesState)
    builder.add_node("tools", make_safe_tool_node(tools))
    builder.add_edge(START, "tools")
    builder.add_edge("tools", END)
    return builder.compile()


@tool
async def slow_tool(x: str) -> str:
    """Test tool that never finishes in time."""
    import asyncio as _asyncio
    await _asyncio.sleep(5.0)
    return "should never get here"


def _ai_message_calling(name: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"x": "hi"}, "id": "call-1", "type": "tool_call"}],
    )


@pytest.mark.asyncio
async def test_real_asyncio_timeout_cancels_a_slow_tool(monkeypatch):
    """slow_tool has no ToolSpec at all -- monkeypatch get_spec so it resolves
    a short, deterministic bound (cooperative_async: real cancel expected)
    instead of waiting out the 60s default."""
    fake_spec = ToolSpec("slow_tool", "compute", 1, False, "none", timeout_seconds=1)
    monkeypatch.setattr(
        "jarvis.graph.safe_tools.get_spec",
        lambda name: fake_spec if name == "slow_tool" else get_spec(name),
    )
    graph = _tool_graph([slow_tool])

    started = time.monotonic()
    result = await graph.ainvoke({"messages": [_ai_message_calling("slow_tool")]})
    elapsed = time.monotonic() - started

    tm = result["messages"][-1]
    assert tm.status == "error"
    assert "category=timeout" in tm.content
    assert "execution_may_still_be_running=false" in tm.content  # cooperative_async
    assert elapsed < 4.0, "wait_for must have bounded the call near the 1s timeout, not the 5s sleep"


# ── Tier 3b: real subprocess.TimeoutExpired, short + deterministic ────────

def test_shell_run_real_subprocess_timeout(isolated_cwd, tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        shell_tools.run("Start-Sleep -Seconds 5", cwd=tmp_path, timeout=1)


def test_python_run_real_subprocess_timeout(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    with pytest.raises(subprocess.TimeoutExpired):
        python_exec.run_script(script, timeout=1)


def test_python_run_timeout_no_longer_returns_a_bare_error_string(tmp_path):
    """Pre-Faz-3 regression guard: run_script() used to catch TimeoutExpired
    itself and return a plain '[ERROR] ... timeout' string that bypassed the
    shared category=timeout/retryable=true/worker_terminated reporting --
    it must now propagate instead."""
    script = tmp_path / "slow2.py"
    script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
    try:
        python_exec.run_script(script, timeout=1)
        pytest.fail("expected subprocess.TimeoutExpired to propagate")
    except subprocess.TimeoutExpired:
        pass


def test_python_run_ordinary_script_still_works_with_default_timeout(tmp_path):
    """No regression for the happy path -- see test_shell_python_guard.py's
    equivalent for the pre-Faz-3 behavior this must not change."""
    script = tmp_path / "plot.py"
    script.write_text('print("hello")\n', encoding="utf-8")
    result = python_exec.run_script(script)
    assert "[OK]" in result and "hello" in result


def test_latex_compile_splits_timeout_across_both_passes(monkeypatch, tmp_path):
    """Unit-level (no real pdflatex needed): confirm each of the two
    subprocess.run() passes gets timeout/2, not the full budget twice."""
    from jarvis.tools import latex

    calls = []

    def _fake_which(name):
        return "pdflatex.exe"

    class _FakeCompletedProcess:
        stdout = ""
        stderr = ""
        returncode = 0

    def _fake_run(cmd, **kwargs):
        calls.append(kwargs.get("timeout"))
        return _FakeCompletedProcess()

    monkeypatch.setattr(latex.shutil, "which", _fake_which)
    monkeypatch.setattr(latex.subprocess, "run", _fake_run)

    tex_path = tmp_path / "doc.tex"
    tex_path.write_text("\\documentclass{article}\\begin{document}x\\end{document}", encoding="utf-8")

    latex.latex_compile(tex_path, timeout=100.0)

    assert calls == [50.0, 50.0]
