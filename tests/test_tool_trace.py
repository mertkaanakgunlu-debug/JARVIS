"""Faz 2.2 — the test-profile tool trace records EVERY tool call (L1 included),
so the eval oracle can prove the right tool actually ran. Gated by
JARVIS_TOOL_TRACE; production writes nothing.
"""
from __future__ import annotations

from langchain_core.messages import ToolMessage

import jarvis.agent as agent_mod
from jarvis import tool_trace
from jarvis.agent import _HudEventCallback


def test_disabled_by_default(monkeypatch, jarvis_home):
    monkeypatch.delenv("JARVIS_TOOL_TRACE", raising=False)
    assert tool_trace.is_enabled() is False
    tool_trace.record(tool="file_read", ok=True)  # no-op
    assert tool_trace.load() == []


def test_record_and_load_round_trip(monkeypatch, jarvis_home):
    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")
    tool_trace.record(tool="web_search", args="ankara hava", ok=True)
    tool_trace.record(tool="file_read", args="x.txt", ok=False)
    rows = tool_trace.load()
    assert [r["tool"] for r in rows] == ["web_search", "file_read"]
    assert rows[0]["ok"] is True and rows[1]["ok"] is False


def test_callback_traces_l1_tool(monkeypatch, jarvis_home):
    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")
    monkeypatch.setattr(agent_mod.event_bus, "tool_call", lambda *a, **k: None)
    cb = _HudEventCallback(transport="test")

    # An L1 read (no risk>=2 audit) must still be traced.
    cb.on_tool_start({"name": "web_search"}, {"query": "python 3.13"}, run_id="rid1")
    cb.on_tool_end(ToolMessage(content="1) python.org ...", tool_call_id="rid1"), run_id="rid1")

    rows = tool_trace.load()
    assert len(rows) == 1
    assert rows[0]["tool"] == "web_search" and rows[0]["ok"] is True


def test_callback_traces_failure(monkeypatch, jarvis_home):
    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")
    monkeypatch.setattr(agent_mod.event_bus, "tool_call", lambda *a, **k: None)
    cb = _HudEventCallback(transport="test")

    cb.on_tool_start({"name": "plot_data"}, {"kind": "line"}, run_id="rid2")
    cb.on_tool_end(ToolMessage(content="[ERROR] Data file not found", tool_call_id="rid2"), run_id="rid2")

    rows = tool_trace.load()
    assert rows[-1]["tool"] == "plot_data" and rows[-1]["ok"] is False


def test_callback_writes_nothing_when_disabled(monkeypatch, jarvis_home):
    monkeypatch.delenv("JARVIS_TOOL_TRACE", raising=False)
    monkeypatch.setattr(agent_mod.event_bus, "tool_call", lambda *a, **k: None)
    cb = _HudEventCallback(transport="test")
    cb.on_tool_start({"name": "web_search"}, {"query": "x"}, run_id="rid3")
    cb.on_tool_end(ToolMessage(content="ok", tool_call_id="rid3"), run_id="rid3")
    assert tool_trace.load() == []


# ── Round 3: the args preview is key-redacted before it hits disk ─────────────
# JARVIS_TOOL_TRACE can be exported outside --profile test, so email bodies,
# file contents and credentials must never persist through the trace.

def test_callback_redacts_sensitive_args(monkeypatch, jarvis_home):
    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")
    monkeypatch.setattr(agent_mod.event_bus, "tool_call", lambda *a, **k: None)
    cb = _HudEventCallback(transport="test")

    cb.on_tool_start(
        {"name": "gmail"},
        {"action": "send", "to": "a@b.c", "subject": "hi", "body": "SECRET-BODY"},
        run_id="rid4",
    )
    cb.on_tool_end(ToolMessage(content="[ERROR] no creds", tool_call_id="rid4"), run_id="rid4")

    row = tool_trace.load()[-1]
    assert "SECRET-BODY" not in row["args"]
    assert "<redacted>" in row["args"]
    assert "a@b.c" in row["args"], "non-sensitive keys must stay readable for the oracle"


def test_redact_helper_masks_by_key_not_position():
    from jarvis.agent import redact_tool_args

    out = redact_tool_args({"path": "x.txt", "content": "gizli metin", "api_key": "AKIA123"})
    assert "gizli metin" not in out and "AKIA123" not in out
    assert "x.txt" in out
    # Non-dict input: truncated, not scanned (documented limit).
    assert redact_tool_args("plain-string-arg") == "plain-string-arg"
