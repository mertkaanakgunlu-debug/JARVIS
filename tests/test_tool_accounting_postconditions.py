"""Agent Runtime rev.2, Faz 3 -- tool_accounting.py's postcondition + timeout-
honesty wiring (the integration layer between postcondition_runner.py/
envelope.py and the actual [TOOL_ERROR] text a tool call produces).
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from jarvis.config import Settings
from jarvis.execution import idempotency
from jarvis.graph.tool_accounting import make_tool_result_accounting_node, parse_timeout_flags


def _ai(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


# ── parse_timeout_flags ──────────────────────────────────────────────────────

def test_parse_timeout_flags_all_false_for_ordinary_content():
    assert parse_timeout_flags("wrote 5 bytes") == (False, False, False)


def test_parse_timeout_flags_reads_all_three():
    content = (
        "[TOOL_ERROR]\ntool=shell_run\ncategory=timeout\nmessage=timed out\n"
        "retryable=true\nexecution_may_still_be_running=true\nworker_terminated=true"
    )
    assert parse_timeout_flags(content) == (True, True, True)


def test_parse_timeout_flags_worker_terminated_independent_of_still_running():
    content = (
        "[TOOL_ERROR]\ntool=research\ncategory=timeout\nmessage=timed out\n"
        "retryable=true\nexecution_may_still_be_running=false\nworker_terminated=false"
    )
    assert parse_timeout_flags(content) == (True, False, False)


# ── real postcondition wiring through the node ──────────────────────────────

@pytest.mark.asyncio
async def test_file_write_success_produces_verified_postconditions(isolated_cwd, tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello")

    settings = Settings(_env_file=None, execution_contract_mode="shadow")
    node = make_tool_result_accounting_node(settings, workspace)
    messages = [
        _ai("file_write", {"path": "a.txt", "content": "hello"}),
        ToolMessage(content="wrote 5 bytes", tool_call_id="call_1"),
    ]

    result = await node({"messages": messages})

    env = result["execution_envelopes"][0]
    assert env["status"] == "success"
    kinds = {p["spec"]["kind"]: p["status"] for p in env["postconditions"]}
    assert kinds == {"file_exists": "verified", "path_within_workspace": "verified"}


@pytest.mark.asyncio
async def test_file_write_missing_output_produces_failed_postcondition(isolated_cwd, tmp_path):
    """A tool that CLAIMS success via its ledger text but whose declared
    output doesn't actually exist -- exactly the class of discrepancy this
    whole initiative exists to surface (nothing acts on it yet, but Faz 3's
    job is to report it honestly)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    # Deliberately do NOT create a.txt on disk.

    settings = Settings(_env_file=None, execution_contract_mode="shadow")
    node = make_tool_result_accounting_node(settings, workspace)
    messages = [
        _ai("file_write", {"path": "a.txt", "content": "hello"}),
        ToolMessage(content="wrote 5 bytes", tool_call_id="call_1"),
    ]

    result = await node({"messages": messages})

    env = result["execution_envelopes"][0]
    kinds = {p["spec"]["kind"]: p["status"] for p in env["postconditions"]}
    assert kinds["file_exists"] == "failed"


@pytest.mark.asyncio
async def test_postconditions_unverified_without_a_workspace(isolated_cwd):
    """Backward-compat contract: workspace=None must not crash -- postcondition
    checks that need a path to resolve report "unverified" instead."""
    settings = Settings(_env_file=None, execution_contract_mode="shadow")
    node = make_tool_result_accounting_node(settings)  # no workspace given
    messages = [
        _ai("file_write", {"path": "a.txt", "content": "hello"}),
        ToolMessage(content="wrote 5 bytes", tool_call_id="call_1"),
    ]

    result = await node({"messages": messages})

    env = result["execution_envelopes"][0]
    kinds = {p["spec"]["kind"]: p["status"] for p in env["postconditions"]}
    assert kinds == {"file_exists": "unverified", "path_within_workspace": "unverified"}


@pytest.mark.asyncio
async def test_tool_with_no_declared_postconditions_gets_empty_list(isolated_cwd, tmp_path):
    settings = Settings(_env_file=None, execution_contract_mode="shadow")
    node = make_tool_result_accounting_node(settings, tmp_path)
    messages = [_ai("file_read", {"path": "a.txt"}), ToolMessage(content="contents", tool_call_id="call_1")]

    result = await node({"messages": messages})

    assert result["execution_envelopes"][0]["postconditions"] == []


@pytest.mark.asyncio
async def test_off_mode_never_runs_postconditions_even_for_file_write(isolated_cwd, tmp_path):
    """The Faz 1 rollback contract extends to Faz 3's additions -- off mode
    must stay a complete no-op, not just for envelopes but for postcondition
    evaluation too (no new code path runs at all)."""
    settings = Settings(_env_file=None, execution_contract_mode="off")
    node = make_tool_result_accounting_node(settings, tmp_path)
    messages = [_ai("file_write", {"path": "a.txt", "content": "x"}), ToolMessage(content="wrote 1 byte", tool_call_id="call_1")]

    result = await node({"messages": messages})

    assert "execution_envelopes" not in result


# ── timeout status honesty ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_timed_out_call_gets_timed_out_envelope_status(isolated_cwd, tmp_path):
    settings = Settings(_env_file=None, execution_contract_mode="shadow")
    node = make_tool_result_accounting_node(settings, tmp_path)
    timeout_block = (
        "[TOOL_ERROR]\ntool=research\ncategory=timeout\nmessage=timed out\n"
        "retryable=true\nexecution_may_still_be_running=false\nworker_terminated=false"
    )
    messages = [
        _ai("research", {"query": "slow question"}),
        ToolMessage(content=timeout_block, tool_call_id="call_1", status="error"),
    ]

    result = await node({"messages": messages})

    env = result["execution_envelopes"][0]
    assert env["status"] == "timed_out"
    assert env["execution_may_still_be_running"] is False
    assert env["worker_terminated"] is False


@pytest.mark.asyncio
async def test_timed_out_call_does_not_commit_to_idempotency_journal(isolated_cwd, tmp_path):
    """A timeout is not a success -- tool_result_accounting's existing
    ok-gated commit must stay ok-gated; this is a regression guard, not new
    behavior, but worth pinning now that timed_out exists as a concept."""
    settings = Settings(_env_file=None, execution_contract_mode="shadow")
    node = make_tool_result_accounting_node(settings, tmp_path)
    timeout_block = "[TOOL_ERROR]\ntool=shell_run\ncategory=timeout\nmessage=timed out\nretryable=true"
    messages = [
        _ai("shell_run", {"command": "Start-Sleep -Seconds 999"}),
        ToolMessage(content=timeout_block, tool_call_id="call_1", status="error"),
    ]

    await node({
        "messages": messages,
        "execution_requests": [{
            "tool_call_id": "call_1",
            "request": {"execution_id": "exec-timeout-test"},
            "signature": "irrelevant-for-this-test",
        }],
    })

    assert idempotency.is_committed("exec-timeout-test") is False
