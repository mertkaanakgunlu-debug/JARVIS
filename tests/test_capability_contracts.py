"""Agent Runtime rev.2, Faz 8 -- per-capability contract tests.

The plan's row: "gecerli arg -> success; bozuk arg -> invalid_args ve GOVDE
CALISMAZ; postcondition ihlali -> failed". test_args_schemas.py already
proves each schema's accept/reject matrix at the pydantic layer; what was
missing is the same matrix driven through the REAL gate -- prepare_execution_
node -- for every schema'd capability, proving per tool that:

  * a valid call gets a signed ExecutionRequest (the contract pipeline's
    entry ticket), and
  * an invalid call gets NO ExecutionRequest at all -- it lands in
    invalid_args_calls, which is precisely why the body can never run: a
    call without a request never reaches dispatch (confirmation_node's
    pre-gate rejects the batch; test_bounded_repair.py proves that half all
    the way through a compiled graph, including the honest final answer).

"Postcondition ihlali -> failed" runs through the one live-wired tool,
file_write -- see test_registry_sweep.py's wiring+runner check and
test_postcondition_runner.py's per-kind coverage.

Every (tool, valid, invalid) triple below is derived from the schema's own
verified dispatch rules in jarvis/execution/args_schemas.py -- each invalid
case violates that tool's SPECIFIC contract (a per-action required field, a
closed Literal, a cross-field XOR), not just a generic junk key. The junk-
key case is covered once, parametrized, at the end (plus property-fuzzed in
test_property_fuzz.py).
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from jarvis.config import Settings
from jarvis.graph.nodes import make_prepare_execution_node
from jarvis.tool_registry import TOOL_SPECS

# (tool, valid args, invalid args, why the invalid one is invalid)
CONTRACT_CASES = [
    ("plot_data",
     {"data_json": "[1, 4, 9]", "kind": "line"},
     {"path": "a.csv", "data_json": "[1]"},
     "path XOR data_json violated (both given)"),
    ("spotify",
     {"action": "play"},
     {"action": "rewind"},
     "action outside the closed Literal"),
    ("google_calendar",
     {"action": "create", "title": "standup", "date": "2026-07-24"},
     {"action": "create", "title": "standup"},
     "create without required 'date'"),
    ("gmail",
     {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"},
     {"action": "send", "to": "a@b.c", "subject": "s"},
     "send without required 'body'"),
    ("hud_panels",
     {"action": "toggle", "panels": "all"},
     {"action": "flip"},
     "action outside the closed Literal"),
    ("schedule",
     {"action": "add", "title": "su ic", "run_at": "09:00"},
     {"action": "add", "title": "su ic"},
     "add without required 'run_at'"),
    ("todo",
     {"action": "add", "title": "rapor"},
     {"action": "edit", "todo_id": "7"},
     "edit with nothing to change"),
    ("google_drive",
     {"action": "share", "file_id": "f1", "email": "a@b.c"},
     {"action": "share", "file_id": "f1"},
     "share without required 'email'"),
    ("itu_mail",
     {"action": "reply", "uid": "42", "body": "tamam"},
     {"action": "reply", "uid": "42"},
     "reply without required 'body'"),
    ("finance",
     {"action": "set_budget", "category": "market", "monthly_limit": 1500.0},
     {"action": "set_budget", "category": "market", "monthly_limit": 0},
     "set_budget with non-positive limit"),
    ("gcp_quota",
     {"action": "usage_today"},
     {"action": "quota"},
     "action outside the closed Literal"),
    ("geo_math",
     {"action": "derive", "expression": "x**2"},
     {"action": "derive"},
     "derive without expression/query/title"),
]

_IDS = [c[0] for c in CONTRACT_CASES]


def test_contract_cases_cover_every_schemad_tool():
    """Completeness guard: a 13th tool gaining an args_schema must show up
    here too -- a capability with a schema but no contract case would be
    exactly the silent coverage gap Faz 8 exists to close."""
    schemad = {n for n, s in TOOL_SPECS.items() if s.args_schema is not None}
    assert schemad == set(_IDS), (
        f"schema'd tools without a contract case: {sorted(schemad - set(_IDS))}; "
        f"contract cases for unschema'd tools: {sorted(set(_IDS) - schemad)}"
    )


def _settings() -> Settings:
    return Settings(_env_file=None, confirmation_gate_enabled=True)


def _ai(tool: str, args: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": tool, "args": args, "id": "c0", "type": "tool_call"},
    ])


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,valid,invalid,why", CONTRACT_CASES, ids=_IDS)
async def test_valid_args_mint_a_signed_request(isolated_cwd, tool, valid, invalid, why):
    node = make_prepare_execution_node(_settings())
    result = await node({"messages": [_ai(tool, valid)]})

    assert result["invalid_args_calls"] == [], (
        f"{tool}: schema rejected the verified-valid case -- over-validation "
        "is its own bug (args_schemas.py's own discipline)"
    )
    reqs = result["execution_requests"]
    assert len(reqs) == 1
    assert reqs[0]["request"]["capability"] == tool
    assert reqs[0]["signature"], "a minted request must be signed"


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,valid,invalid,why", CONTRACT_CASES, ids=_IDS)
async def test_invalid_args_get_no_request_so_the_body_cannot_run(
    isolated_cwd, tool, valid, invalid, why
):
    node = make_prepare_execution_node(_settings())
    result = await node({"messages": [_ai(tool, invalid)]})

    assert result["execution_requests"] == [], (
        f"{tool}: an ExecutionRequest was minted for an invalid call ({why}) -- "
        "the reject-only gate leaked"
    )
    bad = result["invalid_args_calls"]
    assert len(bad) == 1 and bad[0]["capability"] == tool
    errors = bad[0]["errors"]
    assert errors, "the rejection must carry the trimmed pydantic error list"
    for e in errors:
        # The redaction discipline: loc/type/msg only -- never the raw input.
        assert set(e) == {"loc", "type", "msg"}, e


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,valid,invalid,why", CONTRACT_CASES, ids=_IDS)
async def test_unknown_extra_field_is_rejected_per_capability(
    isolated_cwd, tool, valid, invalid, why
):
    """extra='forbid', proven through the gate for each capability with its
    own otherwise-valid args -- an unknown field must not ride along on a
    valid call (the LangChain auto-schema layer would silently accept it;
    see test_langchain_dispatch_coercion.py for that verified gap)."""
    node = make_prepare_execution_node(_settings())
    args = {**valid, "totally_unknown_field": "x"}
    result = await node({"messages": [_ai(tool, args)]})

    assert result["execution_requests"] == []
    assert len(result["invalid_args_calls"]) == 1


@pytest.mark.asyncio
async def test_a_mixed_batch_still_isolates_the_invalid_call(isolated_cwd):
    """One valid + one invalid call in the same batch: the valid one gets its
    request, the invalid one is quarantined -- classification is per call.
    (Whether the WHOLE batch then executes is confirmation_node's wholesale-
    reject decision, locked in by test_bounded_repair.py -- this proves the
    gate's bookkeeping keeps the two piles separate.)"""
    node = make_prepare_execution_node(_settings())
    ai = AIMessage(content="", tool_calls=[
        {"name": "todo", "args": {"action": "add", "title": "t"}, "id": "c0", "type": "tool_call"},
        {"name": "gmail", "args": {"action": "send"}, "id": "c1", "type": "tool_call"},
    ])
    result = await node({"messages": [ai]})

    assert [r["request"]["capability"] for r in result["execution_requests"]] == ["todo"]
    assert [c["capability"] for c in result["invalid_args_calls"]] == ["gmail"]
    assert result["invalid_args_calls"][0]["tool_call_id"] == "c1"
