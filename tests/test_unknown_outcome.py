"""Post-MVP Faz 2.75, Paket D — "timed out" is not "did not happen".

`asyncio.wait_for` stops waiting; it does not stop the socket. For an external
write that difference is a duplicate:

    gmail send -> JARVIS reports a timeout -> the HTTP request completes on
    Google's side anyway -> the user says "tekrar dene" -> the mail goes twice.

The honest field for this has existed since Faz 3 --
`execution_may_still_be_running=true` was already in the block. It sat directly
underneath `retryable=true`, and the model acts on the field it was trained to
act on. Saying "this may have worked" and "go ahead and retry" in one message
is what makes the duplicate.
"""
from __future__ import annotations

import subprocess

import pytest

from jarvis.graph.safe_tools import format_tool_error, is_unknown_outcome
from jarvis.graph.tool_accounting import parse_timeout_flags, parse_unknown_outcome

TIMEOUT = TimeoutError("timed out after 60s")


def _block(tool: str, action: str = "", exc: BaseException = TIMEOUT) -> str:
    return format_tool_error(tool, exc, action)


# ── which calls leave the remote in doubt ─────────────────────────────────────

@pytest.mark.parametrize("tool,action", [
    ("gmail", "send"),
    ("itu_mail", "send"),
    ("google_calendar", "create"),
    ("google_calendar", "delete"),
    ("google_drive", "upload"),
])
def test_a_timed_out_external_write_is_unknown_not_failed(tool, action):
    block = _block(tool, action)
    assert "outcome=unknown" in block
    assert "retryable=false" in block, "an unknown outcome must never invite a retry"
    assert "DO NOT retry" in block


@pytest.mark.parametrize("tool,action", [
    ("gmail", "list_unread"),
    ("gmail", "search"),
    ("google_calendar", "list"),
    ("google_drive", "download"),
])
def test_a_timed_out_external_READ_stays_ordinarily_retryable(tool, action):
    """gmail's ToolSpec is external_write because `send` is. A timed-out
    `list_unread` wrote nothing, and telling the model not to retry it would
    turn a transient network blip into a dead end."""
    block = _block(tool, action)
    assert "outcome=unknown" not in block
    assert "retryable=true" in block


@pytest.mark.parametrize("tool", ["file_write", "report_write", "finance", "index_doc"])
def test_a_converging_write_stays_retryable(tool):
    """idempotency="natural" means re-running lands in the same place. Whether
    the first attempt finished is then not a question worth asking."""
    assert "outcome=unknown" not in _block(tool)
    assert "retryable=true" in _block(tool)


def test_a_local_side_effect_is_not_an_unknown_outcome():
    """A half-finished local write is on this machine, inspectable and
    recoverable. The class this guards is specifically the one that leaves the
    machine."""
    assert "outcome=unknown" not in _block("shell_run")
    assert "outcome=unknown" not in _block("python_run")


def test_cooperative_cancellation_is_not_an_unknown_outcome():
    """The one timeout class where giving up really does stop the work."""
    assert is_unknown_outcome("gmail", "send", still_running=False) is False


def test_an_unregistered_tool_is_not_guessed_at():
    assert is_unknown_outcome("not_a_real_tool", "send", still_running=True) is False


# ── it only applies to timeouts ───────────────────────────────────────────────

@pytest.mark.parametrize("exc,category", [
    (FileNotFoundError("no such file"), "not_found"),
    (ImportError("No module named x"), "dependency_missing"),
    (RuntimeError("401 unauthorized"), "auth"),
])
def test_a_definite_failure_is_still_a_definite_failure(exc, category):
    """An auth error or a missing file DID not happen. Marking those unknown
    would make every real failure unactionable."""
    block = _block("gmail", "send", exc)
    assert f"category={category}" in block
    assert "outcome=unknown" not in block


def test_a_killed_subprocess_still_reports_worker_terminated():
    """The pre-existing fields must survive the new one."""
    block = _block("shell_run", "", subprocess.TimeoutExpired("cmd", 5))
    assert "worker_terminated=true" in block
    assert "execution_may_still_be_running=true" in block


# ── the accounting layer sees it ──────────────────────────────────────────────

def test_the_ledger_parser_reads_the_flag():
    block = _block("gmail", "send")
    assert parse_unknown_outcome(block) is True
    timed_out, may_still_run, _worker = parse_timeout_flags(block)
    assert timed_out and may_still_run


def test_the_parser_does_not_fire_on_an_ordinary_timeout():
    assert parse_unknown_outcome(_block("gmail", "list_unread")) is False


def test_the_parser_tolerates_a_non_string():
    assert parse_unknown_outcome(None) is False
    assert parse_unknown_outcome(["outcome=unknown"]) is True  # str() of a list contains it


# ── the wording the user ends up hearing ──────────────────────────────────────

def test_the_model_is_told_what_to_say_not_just_what_not_to_do():
    """"Do not retry" alone leaves the model to invent a description of what
    happened, and the likeliest invention is "it failed" -- which is the one
    thing that is definitely not established."""
    block = _block("gmail", "send")
    assert "could NOT be verified" in block
    assert "do not report it as failed" in block


# ── the conjunct today's tool set cannot exercise ────────────────────────────

def test_an_idempotent_external_write_is_not_marked_unknown():
    """Every external_write tool shipping today is idempotency="none", so the
    `idempotency == "none"` conjunct is unreachable through the registry -- a
    mutation deleting it survives every other test here.

    It is not decoration. The review's own Paket D asks for exactly this case:
    an external API that accepts an idempotency key can be re-sent safely, and
    then a timeout is an ordinary retry rather than a question about what the
    remote now contains. Registering such a tool is how that gets asserted
    before one exists.
    """
    from jarvis.tool_registry import TOOL_SPECS, ToolSpec, register_dynamic_spec

    name = "keyed_external_sender_for_test"
    register_dynamic_spec(ToolSpec(
        name, "external_api", 3, True, "external_write",
        idempotency="keyed", timeout_class="soft_thread_timeout",
    ))
    try:
        assert is_unknown_outcome(name, "", still_running=True) is False
        assert "outcome=unknown" not in format_tool_error(name, TIMEOUT)
    finally:
        TOOL_SPECS.pop(name, None)


# ── the accounting layer records it, not just parses it ──────────────────────

async def test_the_ledger_row_carries_the_unknown_outcome(tmp_path):
    """A turn's ledger is what a later turn and the audit read. "ok: false"
    alone would tell them it failed."""
    from langchain_core.messages import AIMessage, ToolMessage

    from jarvis.config import Settings
    from jarvis.graph.tool_accounting import make_tool_result_accounting_node

    ai = AIMessage(content="", tool_calls=[
        {"name": "gmail", "args": {"action": "send", "to": "a@b.c"}, "id": "c1"}])
    tm = ToolMessage(
        content=format_tool_error("gmail", TIMEOUT, "send"),
        tool_call_id="c1", name="gmail", status="error",
    )
    node = make_tool_result_accounting_node(
        settings=Settings(_env_file=None, execution_contract_mode="shadow"),
        workspace=tmp_path,
    )
    out = await node({"messages": [ai, tm], "execution_envelopes": []})
    row = out["tool_execution_ledger"][0]
    assert row["ok"] is False
    assert row.get("outcome") == "unknown", row


async def test_an_ordinary_failure_leaves_no_outcome_key(tmp_path):
    """The absence has to mean something too: every failed call carrying
    outcome=unknown would make the flag useless."""
    from langchain_core.messages import AIMessage, ToolMessage

    from jarvis.config import Settings
    from jarvis.graph.tool_accounting import make_tool_result_accounting_node

    ai = AIMessage(content="", tool_calls=[
        {"name": "gmail", "args": {"action": "send"}, "id": "c1"}])
    tm = ToolMessage(
        content=format_tool_error("gmail", RuntimeError("401 unauthorized"), "send"),
        tool_call_id="c1", name="gmail", status="error",
    )
    node = make_tool_result_accounting_node(
        settings=Settings(_env_file=None, execution_contract_mode="shadow"),
        workspace=tmp_path,
    )
    out = await node({"messages": [ai, tm], "execution_envelopes": []})
    assert "outcome" not in out["tool_execution_ledger"][0]
