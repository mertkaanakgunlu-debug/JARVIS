"""Patch 1.2 Faz 1B — deterministic tool-call limits + execution accounting.

Live anchors (2026-07-16 manual round 2):
  A2  — a local model hallucinated a ~20-call batch (2 itu_mail sends) off a
        one-line smalltalk turn; only the external-write gate stood in the way.
        Now the batch-size cap rejects such a batch outright, whatever's in it.
  F16 — procedure_save re-issued ~10 times after succeeding; now the duplicate
        fingerprint check blocks the second identical call, the round budget
        stops the loop shape, and the ledger records what really happened.

Uses isolated_cwd so kill_switch/audit resolve to a fresh data/ dir.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from jarvis.config import Settings
from jarvis.graph.nodes import make_confirmation_node
from jarvis.graph.tool_accounting import (
    make_tool_result_accounting_node,
    tool_call_fingerprint,
    tool_message_ok,
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, confirmation_gate_enabled=True, **overrides)


def _ai(calls: list[tuple[str, dict]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call_{i}", "type": "tool_call"}
            for i, (name, args) in enumerate(calls)
        ],
    )


def _state(calls: list[tuple[str, dict]], **extra) -> dict:
    return {"messages": [_ai(calls)], "transport": "api", **extra}


# ── fingerprint ───────────────────────────────────────────────────────────────

def test_fingerprint_is_arg_order_independent():
    a = tool_call_fingerprint("file_read", {"path": "x", "mode": "r"})
    b = tool_call_fingerprint("file_read", {"mode": "r", "path": "x"})
    assert a == b


def test_fingerprint_distinguishes_args_and_tool():
    base = tool_call_fingerprint("file_read", {"path": "x"})
    assert base != tool_call_fingerprint("file_read", {"path": "y"})
    assert base != tool_call_fingerprint("file_list", {"path": "x"})


# ── batch-size cap (A2 shape) ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_over_limit_batch_rejected_whole(isolated_cwd):
    node = make_confirmation_node(_settings(max_tool_calls_per_ai_message=4))
    calls = [("file_read", {"path": f"f{i}.txt"}) for i in range(5)]

    result = await node(_state(calls))

    assert result["confirmation_result"] == "denied"
    stubs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(stubs) == 5  # every call stubbed — nothing executed
    assert all("BLOCKED" in m.content for m in stubs)
    assert result["tool_calls_attempted"] == 5  # blocked calls still count
    assert result["tool_rounds"] == 1
    # over-limit batch must NOT poison seen: a smaller re-issue stays possible
    assert "seen_tool_fingerprints" not in result


@pytest.mark.asyncio
async def test_batch_at_limit_passes(isolated_cwd):
    node = make_confirmation_node(_settings(max_tool_calls_per_ai_message=4))
    calls = [("file_read", {"path": f"f{i}.txt"}) for i in range(4)]

    result = await node(_state(calls))

    assert result["confirmation_result"] == "approved"
    assert result["tool_calls_attempted"] == 4
    assert len(result["seen_tool_fingerprints"]) == 4


# ── per-turn budget ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_turn_budget_exhaustion_blocks(isolated_cwd):
    node = make_confirmation_node(_settings(max_tool_calls_per_turn=6))
    result = await node(_state(
        [("file_read", {"path": "a"}), ("file_read", {"path": "b"})],
        tool_calls_attempted=5,
    ))

    assert result["confirmation_result"] == "denied"
    assert result["tool_calls_attempted"] == 7  # 5 + 2, blocked still counted


# ── round budget (loop stopper until Faz 2B) ──────────────────────────────────

@pytest.mark.asyncio
async def test_round_budget_blocks_third_round(isolated_cwd):
    node = make_confirmation_node(_settings(max_tool_rounds_per_turn=2))
    result = await node(_state([("file_read", {"path": "a"})], tool_rounds=2))

    assert result["confirmation_result"] == "denied"
    assert result["tool_rounds"] == 3


# ── duplicate fingerprints (F16 shape) ────────────────────────────────────────

@pytest.mark.asyncio
async def test_repeat_of_seen_call_is_blocked(isolated_cwd):
    node = make_confirmation_node(_settings())
    fp = tool_call_fingerprint("file_read", {"path": "a"})

    result = await node(_state(
        [("file_read", {"path": "a"})],
        seen_tool_fingerprints=[fp],
    ))

    assert result["confirmation_result"] == "denied"
    stub = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "[DUPLICATE_TOOL_CALL_BLOCKED]" in stub.content
    assert "was attempted" in stub.content  # not completed → honest wording


@pytest.mark.asyncio
async def test_repeat_of_completed_call_says_completed(isolated_cwd):
    node = make_confirmation_node(_settings())
    fp = tool_call_fingerprint("procedure_save", {"name": "brew_coffee"})

    result = await node(_state(
        [("procedure_save", {"name": "brew_coffee"})],
        seen_tool_fingerprints=[fp],
        completed_tool_fingerprints=[fp],
    ))

    stub = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert "completed successfully" in stub.content


@pytest.mark.asyncio
async def test_duplicate_within_one_batch_blocked(isolated_cwd):
    node = make_confirmation_node(_settings())
    result = await node(_state(
        [("file_read", {"path": "a"}), ("file_read", {"path": "a"})],
    ))

    assert result["confirmation_result"] == "denied"
    contents = [m.content for m in result["messages"] if isinstance(m, ToolMessage)]
    assert any("[DUPLICATE_TOOL_CALL_BLOCKED]" in c for c in contents)
    assert any("[SKIPPED" in c for c in contents)  # the non-dup twin re-issuable


@pytest.mark.asyncio
async def test_different_args_are_not_duplicates(isolated_cwd):
    node = make_confirmation_node(_settings())
    fp = tool_call_fingerprint("file_read", {"path": "a"})

    result = await node(_state(
        [("file_read", {"path": "b"})],
        seen_tool_fingerprints=[fp],
    ))

    assert result["confirmation_result"] == "approved"


# ── counters flow through the normal approve path ─────────────────────────────

@pytest.mark.asyncio
async def test_approved_batch_updates_all_counters(isolated_cwd):
    node = make_confirmation_node(_settings())
    result = await node(_state(
        [("file_read", {"path": "a"})],
        tool_calls_attempted=1, tool_rounds=1,
        seen_tool_fingerprints=[tool_call_fingerprint("file_list", {"path": "."})],
    ))

    assert result["confirmation_result"] == "approved"
    assert result["tool_calls_attempted"] == 2
    assert result["tool_rounds"] == 2
    assert len(result["seen_tool_fingerprints"]) == 2  # old + new


# ── tool_result_accounting node ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_accounting_promotes_only_successes():
    node = make_tool_result_accounting_node()
    ai = _ai([("file_read", {"path": "a"}), ("itu_mail", {"action": "list_unread"})])
    msgs = [
        ai,
        ToolMessage(content="file contents here", tool_call_id="call_0"),
        ToolMessage(
            content="[TOOL_ERROR]\ntool=itu_mail\ncategory=dependency_missing\n"
                    "message=imap-tools not installed\nretryable=false",
            tool_call_id="call_1",
        ),
    ]

    result = await node({"messages": msgs})

    ok_fp = tool_call_fingerprint("file_read", {"path": "a"})
    bad_fp = tool_call_fingerprint("itu_mail", {"action": "list_unread"})
    assert result["completed_tool_fingerprints"] == [ok_fp]
    ledger = result["tool_execution_ledger"]
    assert len(ledger) == 2
    by_fp = {e["fingerprint"]: e for e in ledger}
    assert by_fp[ok_fp]["ok"] is True
    assert by_fp[bad_fp]["ok"] is False


@pytest.mark.asyncio
async def test_accounting_appends_to_existing_state():
    node = make_tool_result_accounting_node()
    prior_fp = tool_call_fingerprint("web_search", {"q": "x"})
    ai = _ai([("file_read", {"path": "a"})])
    msgs = [ai, ToolMessage(content="ok", tool_call_id="call_0")]

    result = await node({
        "messages": msgs,
        "completed_tool_fingerprints": [prior_fp],
        "tool_execution_ledger": [{"tool": "web_search", "fingerprint": prior_fp, "ok": True, "content_head": ""}],
    })

    assert result["completed_tool_fingerprints"][0] == prior_fp
    assert len(result["completed_tool_fingerprints"]) == 2
    assert len(result["tool_execution_ledger"]) == 2


@pytest.mark.asyncio
async def test_accounting_noop_without_tool_calls():
    node = make_tool_result_accounting_node()
    assert await node({"messages": [AIMessage(content="hi")]}) == {}


def test_tool_message_ok_matrix():
    ok = ToolMessage(content="fine", tool_call_id="x")
    assert tool_message_ok(ok) is True
    assert tool_message_ok(None) is False
    assert tool_message_ok(ToolMessage(content="[BLOCKED: nope]", tool_call_id="x")) is False
    assert tool_message_ok(ToolMessage(content="[ERROR] boom", tool_call_id="x")) is False
    assert tool_message_ok(ToolMessage(content="ok", tool_call_id="x", status="error")) is False


# ── recursion-stop message is ledger-based, never a blanket claim ─────────────

class _FakeGraphWithState:
    def __init__(self, values: dict):
        self._values = values

    async def aget_state(self, config):
        from types import SimpleNamespace
        return SimpleNamespace(values=self._values)


class _FakeAgentShell:
    """Just enough of JarvisAgent to run _recursion_stop_response unbound."""
    def __init__(self, ledger):
        self._graph = _FakeGraphWithState({"tool_execution_ledger": ledger})


@pytest.mark.asyncio
async def test_recursion_message_reports_successes(isolated_cwd):
    from jarvis.agent import JarvisAgent
    shell = _FakeAgentShell([
        {"tool": "procedure_save", "fingerprint": "f1", "ok": True, "content_head": ""},
        {"tool": "procedure_save", "fingerprint": "f1", "ok": False, "content_head": ""},
    ])
    msg = await JarvisAgent._recursion_stop_response(shell, {}, "api")
    assert "Başarıyla tamamlanan işlemler korundu" in msg
    assert "procedure_save" in msg


@pytest.mark.asyncio
async def test_recursion_message_honest_when_nothing_succeeded(isolated_cwd):
    from jarvis.agent import JarvisAgent
    shell = _FakeAgentShell([
        {"tool": "itu_mail", "fingerprint": "f1", "ok": False, "content_head": ""},
    ])
    msg = await JarvisAgent._recursion_stop_response(shell, {}, "api")
    assert "doğrulanamadı" in msg
    assert "korundu" not in msg


@pytest.mark.asyncio
async def test_recursion_message_survives_missing_checkpoint(isolated_cwd):
    from jarvis.agent import JarvisAgent

    class _Broken:
        _graph = None  # aget_state attribute access will raise

    msg = await JarvisAgent._recursion_stop_response(_Broken(), {}, "api")
    assert "doğrulanamadı" in msg
