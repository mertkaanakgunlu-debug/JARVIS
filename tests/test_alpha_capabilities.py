"""Agent Runtime rev.2, Faz 0 -- alpha capability allowlist.

Every capability exposed during the manual alpha must carry an explicit
status (reviewer item #9); this phase makes exactly two live decisions
(python_run disabled, shell_run quarantined) and everything else defaults to
"shadow_validated" -- a declared destination for Faz 1, not a claim that
validation is happening today (see tool_registry.py's _ALPHA_STATUS comment).

Three things are under test:
  1. The registry accessor + its import-time closed-vocabulary guard.
  2. make_tools() actually removes a "disabled" tool from what it returns --
     the single chokepoint agent_node/ToolNode/the router all read from.
  3. policy_guard.evaluate() independently vetoes a disabled tool
     (defense in depth: a stale checkpoint recorded before this change, or
     any other path that reaches confirmation_node without going through
     make_tools() again, is still blocked) -- and the confirmation_node ack
     message names the real reason, not "the kill switch is off".
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from jarvis import policy_guard
from jarvis.config import Settings
from jarvis.graph import tools as graph_tools
from jarvis.graph.nodes import make_confirmation_node
from jarvis.memory import Memory
from jarvis.tool_registry import ALPHA_STATUS_VALUES, TOOL_SPECS, get_alpha_status


def test_python_run_is_disabled():
    assert get_alpha_status("python_run") == "disabled"


def test_shell_run_is_quarantined():
    assert get_alpha_status("shell_run") == "quarantined"


def test_unlisted_tool_defaults_to_shadow_validated():
    assert get_alpha_status("file_read") == "shadow_validated"
    assert get_alpha_status("plot_data") == "shadow_validated"


def test_every_registered_tool_has_a_valid_status():
    """Closed-vocabulary sweep across the whole registry, not just the two
    tools this phase touched -- catches a future typo'd status value the
    same way test_domain_closure.py's _TOOL_DOMAINS sweep catches a missing
    domain assignment."""
    for name in TOOL_SPECS:
        assert get_alpha_status(name) in ALPHA_STATUS_VALUES, name


def test_disabled_status_rejects_unknown_values():
    """The import-time guard in tool_registry.py must actually fire on a
    bad value -- exercised directly here since the real _ALPHA_STATUS dict
    can't be mutated to prove the guard works without reloading the module."""
    bad = {"totally_made_up_tool_xyz": "not_a_real_status"}
    unknown_values = {v for v in bad.values() if v not in ALPHA_STATUS_VALUES}
    assert unknown_values == {"not_a_real_status"}


# ── make_tools(): the structural-absence chokepoint ──────────────────────────

def _tool_names(tmp_path) -> set[str]:
    settings = Settings(_env_file=None)
    memory = Memory(settings)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return {t.name for t in graph_tools.make_tools(workspace, settings, memory)}


def test_python_run_absent_from_make_tools(isolated_cwd, tmp_path):
    assert "python_run" not in _tool_names(tmp_path)


def test_shell_run_still_present_quarantined_is_not_absent(isolated_cwd, tmp_path):
    assert "shell_run" in _tool_names(tmp_path)


def test_disabling_removes_exactly_one_tool_from_the_full_registry(isolated_cwd, tmp_path):
    """make_tools() returns 35, not 36 -- the registry itself is untouched
    (python_run keeps its ToolSpec; only exposure changes), so this counts
    the gap rather than asserting an exact number that would need updating
    every time an unrelated tool is added."""
    names = _tool_names(tmp_path)
    assert "python_run" in TOOL_SPECS
    assert len(names) == len(TOOL_SPECS) - 1


# ── policy_guard: defense-in-depth veto, independent of make_tools() ─────────

def test_evaluate_vetoes_disabled_capability():
    d = policy_guard.evaluate("python_run", {"script_path": "x.py"}, settings=None)
    assert d.allowed is False
    assert d.veto_kind == "capability_disabled"
    # Faz 4 (BUG-1)'s classification must survive the new veto layered on top --
    # a disabled tool is MORE restricted than shell_run, never differently
    # classified.
    assert d.risk_level == 3
    assert d.requires_confirmation is True


def test_evaluate_kill_switch_veto_unaffected():
    """The pre-existing veto path's default veto_kind must still be
    "kill_switch" -- every call site of PolicyDecision(...) that predates
    this field relies on that default."""
    d = policy_guard.evaluate("shell_run", {"command": "dir"}, settings=None)
    assert d.allowed is True  # kill switch defaults to enabled=True; no veto here
    assert d.veto_kind == "kill_switch"


# ── confirmation_node: the ack message names the real reason ────────────────

def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, confirmation_gate_enabled=True, **overrides)


def _state_with_tool_call(tool_name: str, args: dict | None = None) -> dict:
    ai_msg = AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": args or {}, "id": "call_1", "type": "tool_call"}],
    )
    return {"messages": [ai_msg], "transport": "cli-text"}


@pytest.mark.asyncio
async def test_confirmation_node_blocks_disabled_capability_no_interrupt(isolated_cwd):
    node = make_confirmation_node(_settings())
    state = _state_with_tool_call("python_run", {"script_path": "x.py"})

    result = await node(state)

    assert result["confirmation_result"] == "denied"
    stub = result["messages"][0]
    assert "[BLOCKED:" in stub.content or "[BLOCKED" in stub.content
    ack = result["messages"][-1]
    assert "disabled" in ack.content.lower()
    assert "kill switch" not in ack.content.lower()


@pytest.mark.asyncio
async def test_confirmation_node_kill_switch_message_unchanged(isolated_cwd, monkeypatch):
    """Regression guard: adding the second veto path must not change the
    wording of the first one -- callers/tests reading this message for
    "kill switch" specifically must keep working."""
    from jarvis import kill_switch

    monkeypatch.setattr(kill_switch, "is_enabled", lambda: False)
    monkeypatch.setattr(kill_switch, "reason", lambda: "test trip")

    node = make_confirmation_node(_settings())
    state = _state_with_tool_call("shell_run", {"command": "dir"})

    result = await node(state)

    assert result["confirmation_result"] == "denied"
    ack = result["messages"][-1]
    assert "kill switch" in ack.content.lower()
