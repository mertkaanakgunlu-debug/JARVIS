"""Faz 1.1 regression — the audit callback's ``ok`` flag must reflect the REAL
tool outcome, read from ``ToolMessage.content``.

Before this fix ``_record_execution_end`` did ``str(output)``; ``output`` is a
ToolMessage object whose ``str()`` starts with ``content='[ERROR]...'``, so the
``startswith("[ERROR]")`` guard never matched and every failed call (the B6
grafik ``[ERROR] Data file not found``, plus Calendar/Gmail credential errors)
was logged as ``ok:true``. The fix judges ``.content`` with the same canonical
``content_is_failure`` helper the execution ledger uses.

audit_log.record is monkeypatched to capture kwargs, so nothing touches the
real ``data/`` dir (no isolated_cwd needed here).
"""
from __future__ import annotations

from langchain_core.messages import ToolMessage

import jarvis.agent as agent_mod
from jarvis.agent import _HudEventCallback


def _capture(monkeypatch) -> list[dict]:
    records: list[dict] = []
    monkeypatch.setattr(
        agent_mod.audit_log, "record",
        lambda event, **kw: records.append({"event": event, **kw}),
    )
    # on_tool_end also pings the HUD feed — keep it inert in the test.
    monkeypatch.setattr(agent_mod.event_bus, "tool_call", lambda *a, **k: None)
    return records


def _fire_end(cb: _HudEventCallback, run_id: str, output) -> None:
    # Seed the pending map the way on_tool_start would for a risk>=2 tool, then
    # fire the end hook (white-box: exercises exactly the ok computation).
    cb._audit_pending[str(run_id)] = ("gmail", 3)
    cb.on_tool_end(output, run_id=run_id)


def _last_end_ok(records: list[dict]):
    ends = [r for r in records if r["event"] == "execution_end"]
    assert ends, "no execution_end record was written"
    return ends[-1]["ok"]


def test_error_content_logs_not_ok(monkeypatch):
    records = _capture(monkeypatch)
    cb = _HudEventCallback(transport="test")
    _fire_end(cb, "r1", ToolMessage(
        content="[ERROR] Data file not found: jarvis_test.csv", tool_call_id="r1"))
    assert _last_end_ok(records) is False


def test_warning_prefix_content_logs_not_ok(monkeypatch):
    records = _capture(monkeypatch)
    cb = _HudEventCallback(transport="test")
    _fire_end(cb, "r2", ToolMessage(content="⚠ title gerekli.", tool_call_id="r2"))
    assert _last_end_ok(records) is False


def test_success_content_logs_ok(monkeypatch):
    records = _capture(monkeypatch)
    cb = _HudEventCallback(transport="test")
    _fire_end(cb, "r3", ToolMessage(
        content="Dosya oluşturuldu: jarvis_test.txt", tool_call_id="r3"))
    assert _last_end_ok(records) is True


def test_error_status_logs_not_ok(monkeypatch):
    records = _capture(monkeypatch)
    cb = _HudEventCallback(transport="test")
    _fire_end(cb, "r4", ToolMessage(content="whatever", tool_call_id="r4", status="error"))
    assert _last_end_ok(records) is False


def test_raw_string_error_still_detected(monkeypatch):
    # Defensive: if a tool ever returns a raw string instead of a ToolMessage,
    # getattr(output, "content", output) falls back to the string itself.
    records = _capture(monkeypatch)
    cb = _HudEventCallback(transport="test")
    _fire_end(cb, "r5", "[BLOCKED] denied pattern")
    assert _last_end_ok(records) is False


# ── Agent Runtime rev.2, Faz 1 — args_preview/result_preview are now
# redacted, not raw. Both used to be str(x)[:200] with no redaction at all,
# even though the SAME input_str was already key-redacted for tool_trace
# two lines below the args_preview call site (see agent.py's on_tool_start).

def test_start_audit_args_preview_is_redacted(monkeypatch):
    records = _capture(monkeypatch)
    cb = _HudEventCallback(transport="test")

    cb.on_tool_start(
        {"name": "gmail"},
        {"action": "send", "to": "a@b.c", "body": "SECRET-BODY"},
        run_id="r6",
    )

    starts = [r for r in records if r["event"] == "execution_start"]
    assert starts, "no execution_start record was written"
    assert "SECRET-BODY" not in starts[-1]["args_preview"]
    assert "a@b.c" in starts[-1]["args_preview"], "non-sensitive fields must stay readable"


def test_end_audit_result_preview_is_redacted(monkeypatch):
    records = _capture(monkeypatch)
    cb = _HudEventCallback(transport="test")
    _fire_end(cb, "r7", ToolMessage(
        content="sent, Authorization: Bearer sk-abcdef1234567890ABCDEF echoed back",
        tool_call_id="r7",
    ))

    ends = [r for r in records if r["event"] == "execution_end"]
    assert "sk-abcdef1234567890ABCDEF" not in ends[-1]["result_preview"]
