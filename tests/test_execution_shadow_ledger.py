"""Agent Runtime rev.2, Faz 1 -- the shadow ledger wired into
tool_result_accounting (jarvis/graph/tool_accounting.py).

Central invariant under test: with execution_contract_mode="off" (the
default) or settings=None, this node's behavior must be byte-identical to
pre-Faz-1 code -- no execution_envelopes key, same completed_tool_
fingerprints/tool_execution_ledger as tests/test_tool_limits.py already
pins. Any non-"off" mode additionally builds one ExecutionEnvelope per call
as a pure observer, without changing those two pre-existing fields at all
(the "no decision changes" acceptance criterion from the plan's Faz 1
section) -- see the mode-comparison test at the bottom for the literal
bit-identical check.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from jarvis.config import Settings
from jarvis.graph.tool_accounting import make_tool_result_accounting_node


def _ai(calls: list[tuple[str, dict]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call_{i}", "type": "tool_call"}
            for i, (name, args) in enumerate(calls)
        ],
    )


def _messages():
    ai = _ai([("file_read", {"path": "a"}), ("itu_mail", {"action": "list_unread"})])
    return [
        ai,
        ToolMessage(content="file contents here", tool_call_id="call_0"),
        ToolMessage(
            content="[TOOL_ERROR]\ntool=itu_mail\ncategory=dependency_missing\n"
                    "message=imap-tools not installed\nretryable=false",
            tool_call_id="call_1",
        ),
    ]


# ── off / no settings: zero new code path ───────────────────────────────────

@pytest.mark.asyncio
async def test_no_settings_produces_no_envelopes_key():
    node = make_tool_result_accounting_node()
    result = await node({"messages": _messages()})
    assert "execution_envelopes" not in result


@pytest.mark.asyncio
async def test_mode_off_produces_no_envelopes_key():
    settings = Settings(_env_file=None, execution_contract_mode="off")
    node = make_tool_result_accounting_node(settings)
    result = await node({"messages": _messages()})
    assert "execution_envelopes" not in result


# ── shadow: envelopes appear, one per call, as pure observation ────────────

@pytest.mark.asyncio
async def test_mode_shadow_builds_one_envelope_per_call():
    settings = Settings(_env_file=None, execution_contract_mode="shadow")
    node = make_tool_result_accounting_node(settings)
    result = await node({"messages": _messages()})

    envelopes = result["execution_envelopes"]
    assert len(envelopes) == 2
    by_capability = {e["capability"]: e for e in envelopes}
    assert by_capability["file_read"]["status"] == "success"
    assert by_capability["itu_mail"]["status"] == "failed"
    assert by_capability["itu_mail"]["retryable"] is False  # _messages()'s content says retryable=false


@pytest.mark.asyncio
async def test_shadow_envelope_execution_id_is_the_tool_call_id():
    settings = Settings(_env_file=None, execution_contract_mode="shadow")
    node = make_tool_result_accounting_node(settings)
    result = await node({"messages": _messages()})
    ids = {e["execution_id"] for e in result["execution_envelopes"]}
    assert ids == {"call_0", "call_1"}


@pytest.mark.asyncio
async def test_shadow_envelope_never_carries_raw_argument_values():
    ai = _ai([("gmail", {"action": "send", "to": "a@b.c", "body": "SECRET-BODY"})])
    messages = [ai, ToolMessage(content="sent", tool_call_id="call_0")]
    settings = Settings(_env_file=None, execution_contract_mode="shadow")
    node = make_tool_result_accounting_node(settings)

    result = await node({"messages": messages})

    assert "SECRET-BODY" not in str(result["execution_envelopes"])


@pytest.mark.asyncio
async def test_shadow_appends_to_existing_envelopes_state():
    settings = Settings(_env_file=None, execution_contract_mode="shadow")
    node = make_tool_result_accounting_node(settings)
    ai = _ai([("file_read", {"path": "a"})])
    messages = [ai, ToolMessage(content="ok", tool_call_id="call_0")]

    result = await node({
        "messages": messages,
        "execution_envelopes": [{"execution_id": "prior", "capability": "web_search"}],
    })

    assert len(result["execution_envelopes"]) == 2
    assert result["execution_envelopes"][0]["execution_id"] == "prior"


@pytest.mark.parametrize("mode", ["enforce_read_only", "enforce_reversible", "enforce_all"])
@pytest.mark.asyncio
async def test_enforce_modes_currently_behave_like_shadow(mode):
    """Faz 1 only distinguishes off vs not-off -- documented in both
    config.py and tool_accounting.py. Any enforce_* value must still just
    observe, not gate (Faz 2's job), until that phase adds real branching."""
    settings = Settings(_env_file=None, execution_contract_mode=mode)
    node = make_tool_result_accounting_node(settings)
    result = await node({"messages": _messages()})
    assert len(result["execution_envelopes"]) == 2


# ── the actual "no decision changes" acceptance criterion ──────────────────

@pytest.mark.asyncio
async def test_off_and_shadow_agree_on_every_pre_existing_field():
    """The Faz 1 acceptance bar, at unit-test scale: turning shadow mode on
    must not change completed_tool_fingerprints or the ok/fingerprint/tool
    shape of tool_execution_ledger -- only ADD the new envelopes key."""
    off_node = make_tool_result_accounting_node(
        Settings(_env_file=None, execution_contract_mode="off")
    )
    shadow_node = make_tool_result_accounting_node(
        Settings(_env_file=None, execution_contract_mode="shadow")
    )

    off_result = await off_node({"messages": _messages()})
    shadow_result = await shadow_node({"messages": _messages()})

    assert off_result["completed_tool_fingerprints"] == shadow_result["completed_tool_fingerprints"]
    off_ledger = [{k: v for k, v in e.items() if k != "content_head"} for e in off_result["tool_execution_ledger"]]
    shadow_ledger = [{k: v for k, v in e.items() if k != "content_head"} for e in shadow_result["tool_execution_ledger"]]
    assert off_ledger == shadow_ledger
    assert "execution_envelopes" not in off_result
    assert len(shadow_result["execution_envelopes"]) == 2
