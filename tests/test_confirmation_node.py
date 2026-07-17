"""jarvis/graph/nodes.py's make_confirmation_node -- Faz 2 of the GPT-5.6
review remediation plan ("proactive turn not structurally read-only").

Before this fix, an L2 tool (risk_level=2, requires_confirmation=False by
spec -- e.g. file_write/procedure_save, tools designed to auto-approve in an
interactive turn because a human notices them in the transcript) executed
unsupervised during a monitor.py-initiated proactive turn too, since nothing
but a system-prompt instruction told the model not to call it. The already-
existing L3 (requires_confirmation=True) path -- discard the interrupt into a
"needs_confirmation" notification instead of executing -- was already correct
and must stay that way; these tests cover both: the gap is closed, and the
working path isn't regressed.

Uses isolated_cwd so kill_switch resolves to its true "no data/kill_switch.json
yet" default (enabled=True) rather than inheriting state from another test.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage
from langgraph.errors import GraphInterrupt

from jarvis.config import Settings
from jarvis.graph.nodes import make_confirmation_node


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, confirmation_gate_enabled=True, **overrides)


def _state_with_tool_call(tool_name: str, transport: str, args: dict | None = None) -> dict:
    ai_msg = AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": args or {}, "id": "call_1", "type": "tool_call"}],
    )
    return {"messages": [ai_msg], "transport": transport}


@pytest.mark.asyncio
async def test_proactive_blocks_l2_auto_approve_tool(isolated_cwd):
    node = make_confirmation_node(_settings())
    state = _state_with_tool_call("file_write", "monitor-email")

    result = await node(state)

    assert result["confirmation_result"] == "denied"
    assert any("not executed" in m.content for m in result["messages"] if hasattr(m, "content"))


@pytest.mark.asyncio
async def test_proactive_blocks_procedure_save(isolated_cwd):
    node = make_confirmation_node(_settings())
    state = _state_with_tool_call("procedure_save", "monitor-calendar")

    result = await node(state)

    assert result["confirmation_result"] == "denied"


@pytest.mark.asyncio
async def test_proactive_allows_read_only_tool(isolated_cwd):
    node = make_confirmation_node(_settings())
    state = _state_with_tool_call("file_read", "monitor-email")

    result = await node(state)

    assert result["confirmation_result"] == "approved"


@pytest.mark.asyncio
async def test_proactive_l3_still_interrupts_instead_of_silent_block(isolated_cwd, monkeypatch):
    """The pre-existing, verified-correct behavior for a genuinely-confirmable
    (requires_confirmation=True, gate enabled) L3 call: proactive_turn()
    catches this GraphInterrupt and reports ProactiveOutcome(kind=
    "needs_confirmation") -- more informative than a flat block, so this path
    must NOT be replaced by the new structural block.

    make_confirmation_node does `from langgraph.types import interrupt as
    _interrupt` at call time -- patching the langgraph.types attribute before
    invoking it is what real langgraph interrupt/resume machinery a raw node
    call outside a compiled graph's checkpointed run can't provide, without
    pulling in a full graph build just for this one assertion. The point
    under test is only "did confirmation_node reach the real gate instead of
    silently short-circuiting it", not langgraph's own resume semantics
    (untouched by this change, exercised elsewhere via proactive_turn()).
    """
    reached = []

    def _fake_interrupt(payload):
        reached.append(payload)
        raise GraphInterrupt()

    monkeypatch.setattr("langgraph.types.interrupt", _fake_interrupt)

    node = make_confirmation_node(_settings())
    state = _state_with_tool_call("shell_run", "monitor-schedule")

    with pytest.raises(GraphInterrupt):
        await node(state)
    assert reached, "expected the real confirmation interrupt to be reached, not a structural block"


@pytest.mark.asyncio
async def test_interactive_transport_l2_tool_still_auto_approves(isolated_cwd):
    """No regression for real (non-proactive) turns -- L2 tools keep their
    existing no-confirmation-needed behavior when a human is present."""
    node = make_confirmation_node(_settings())
    state = _state_with_tool_call("file_write", "cli-text")

    result = await node(state)

    assert result["confirmation_result"] == "approved"


# ── external_writes_enabled (stabilization sprint, --profile test) ───────────

@pytest.mark.asyncio
async def test_external_writes_disabled_blocks_gmail_before_interrupt(isolated_cwd, monkeypatch):
    """The structural guarantee behind --profile test: an external_write tool
    is denied before ever reaching the interrupt -- no prompt hangs waiting
    for a human that isn't there in a scripted/CI run."""
    def _fail_if_reached(payload):
        raise AssertionError("must not reach the interrupt when external writes are disabled")
    monkeypatch.setattr("langgraph.types.interrupt", _fail_if_reached)

    node = make_confirmation_node(_settings(external_writes_enabled=False))
    state = _state_with_tool_call("gmail", "cli-text", {"action": "send", "to": "x@example.com"})

    result = await node(state)

    assert result["confirmation_result"] == "denied"
    assert any("external writes are disabled" in m.content
               for m in result["messages"] if hasattr(m, "content"))


@pytest.mark.asyncio
async def test_external_writes_disabled_blocks_spotify_even_though_l2(isolated_cwd):
    """spotify is risk_level=2 (normally auto-approves, no interrupt at all)
    -- external_writes_enabled=False must still catch it: the gate is keyed
    on ToolSpec.side_effect_type=="external_write", not on risk level."""
    node = make_confirmation_node(_settings(external_writes_enabled=False))
    state = _state_with_tool_call("spotify", "cli-text", {"action": "play", "query": "test"})

    result = await node(state)

    assert result["confirmation_result"] == "denied"


@pytest.mark.asyncio
async def test_external_writes_disabled_does_not_block_local_write(isolated_cwd):
    """Scope check: local writes (file_write, side_effect_type=="local_write")
    must stay reachable under the profile -- only external_write is gated, so
    tool-calling itself remains testable."""
    node = make_confirmation_node(_settings(external_writes_enabled=False))
    state = _state_with_tool_call("file_write", "cli-text")

    result = await node(state)

    assert result["confirmation_result"] == "approved"


# ── Patch 1.1: the gate is per-ACTION, not per-ToolSpec ───────────────────────
# gmail/calendar/drive/itu_mail are spec'd side_effect_type="external_write"
# wholesale because they mix read and write actions under one tool. The old
# static-spec check therefore denied `gmail read` / `calendar list` too --
# blinding the test profile to exactly the read paths it should exercise.

@pytest.mark.asyncio
async def test_external_writes_disabled_allows_gmail_read(isolated_cwd):
    node = make_confirmation_node(_settings(external_writes_enabled=False))
    state = _state_with_tool_call("gmail", "cli-text", {"action": "read", "query": "in:inbox"})

    result = await node(state)

    assert result["confirmation_result"] == "approved"


@pytest.mark.asyncio
async def test_external_writes_disabled_allows_calendar_list(isolated_cwd):
    node = make_confirmation_node(_settings(external_writes_enabled=False))
    state = _state_with_tool_call("google_calendar", "cli-text", {"action": "list"})

    result = await node(state)

    assert result["confirmation_result"] == "approved"


@pytest.mark.asyncio
async def test_external_writes_disabled_still_blocks_calendar_create(isolated_cwd, monkeypatch):
    """The mirror case: on the same mixed tool, a WRITE action must stay
    hard-denied before the interrupt."""
    def _fail_if_reached(payload):
        raise AssertionError("must not reach the interrupt when external writes are disabled")
    monkeypatch.setattr("langgraph.types.interrupt", _fail_if_reached)

    node = make_confirmation_node(_settings(external_writes_enabled=False))
    state = _state_with_tool_call("google_calendar", "cli-text",
                                  {"action": "create", "title": "Standup"})

    result = await node(state)

    assert result["confirmation_result"] == "denied"
    assert any("external writes are disabled" in m.content
               for m in result["messages"] if hasattr(m, "content"))


@pytest.mark.asyncio
async def test_external_writes_disabled_allows_drive_download(isolated_cwd):
    node = make_confirmation_node(_settings(external_writes_enabled=False))
    state = _state_with_tool_call("google_drive", "cli-text",
                                  {"action": "download", "file_id": "abc123"})

    result = await node(state)

    assert result["confirmation_result"] == "approved"


@pytest.mark.asyncio
async def test_external_writes_enabled_default_true_no_behavior_change(isolated_cwd, monkeypatch):
    """Normal runs (the field's default) must be completely unaffected --
    gmail still reaches the real interrupt exactly as before this sprint."""
    from langgraph.errors import GraphInterrupt
    reached = []

    def _fake_interrupt(payload):
        reached.append(payload)
        raise GraphInterrupt()
    monkeypatch.setattr("langgraph.types.interrupt", _fake_interrupt)

    node = make_confirmation_node(_settings())  # external_writes_enabled defaults True
    state = _state_with_tool_call("gmail", "cli-text", {"action": "send", "to": "x@example.com"})

    with pytest.raises(GraphInterrupt):
        await node(state)
    assert reached
