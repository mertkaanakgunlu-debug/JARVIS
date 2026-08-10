"""Agent Runtime rev.2, Faz 2 -- prepare_execution_node, and confirmation_node's
new post-approval re-verification + idempotency check.

Three tiers, same shape this suite already uses for Faz 0-1 (test_alpha_
capabilities.py, test_execution_shadow_ledger.py):
  1. prepare_execution_node in isolation -- what ExecutionRequest it builds.
  2. prepare_execution_node chained into confirmation_node -- the actual new
     security checks (TOCTOU re-verify, idempotency replay guard), plus the
     backward-compat guarantee that confirmation_node is unchanged when
     execution_requests is absent (old checkpoint / a test that skips
     straight to confirmation_node, as every pre-Faz-2 test in this suite
     does).
  3. One real compiled-graph interrupt -> Command(resume=...) round trip,
     proving the new agent -> prepare_execution -> confirmation topology
     actually wires up end to end (nothing in tier 1/2 drives a real graph).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.errors import GraphInterrupt

from jarvis.config import Settings
from jarvis.execution import approval, idempotency
from jarvis.execution.request import ExecutionRequest
from jarvis.graph.nodes import make_confirmation_node, make_prepare_execution_node
from jarvis.graph.tool_accounting import make_tool_result_accounting_node, tool_call_fingerprint


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, confirmation_gate_enabled=True, **overrides)


def _ai_tool_call(tool_name: str, args: dict | None = None, call_id: str = "call_1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": args or {}, "id": call_id, "type": "tool_call"}],
    )


# ── Tier 1: prepare_execution_node in isolation ─────────────────────────────

@pytest.mark.asyncio
async def test_no_tool_call_is_a_noop():
    node = make_prepare_execution_node(_settings())
    result = await node({"messages": [HumanMessage(content="merhaba")]})
    assert result == {}


@pytest.mark.asyncio
async def test_builds_one_request_per_call(isolated_cwd):
    node = make_prepare_execution_node(_settings())
    ai = AIMessage(content="", tool_calls=[
        {"name": "file_read", "args": {"path": "a.txt"}, "id": "c0", "type": "tool_call"},
        {"name": "file_write", "args": {"path": "b.txt", "content": "x"}, "id": "c1", "type": "tool_call"},
    ])
    result = await node({"messages": [ai]})

    reqs = result["execution_requests"]
    assert len(reqs) == 2
    assert {r["tool_call_id"] for r in reqs} == {"c0", "c1"}


@pytest.mark.asyncio
async def test_request_fields_mirror_policy_guard(isolated_cwd):
    """risk_level/requires_confirmation/allowed/side_effect_type must match
    what policy_guard.evaluate() itself would say for this exact call --
    prepare_execution is not a second, divergent classifier."""
    from jarvis import policy_guard

    node = make_prepare_execution_node(_settings())
    ai = _ai_tool_call("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"})
    result = await node({"messages": [ai]})

    entry = result["execution_requests"][0]["request"]
    decision = policy_guard.evaluate("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}, _settings())
    assert entry["risk_level"] == decision.risk_level
    assert entry["requires_confirmation"] == decision.requires_confirmation
    assert entry["allowed"] == decision.allowed
    assert entry["side_effect_type"] == decision.side_effect_type


@pytest.mark.asyncio
async def test_disabled_capability_is_marked_not_allowed(isolated_cwd):
    """python_run: Faz 0's alpha veto must show up in allowed=False here too
    -- prepare_execution reads the same policy_guard.evaluate()."""
    node = make_prepare_execution_node(_settings())
    ai = _ai_tool_call("python_run", {"script_path": "x.py"})
    result = await node({"messages": [ai]})

    entry = result["execution_requests"][0]["request"]
    assert entry["allowed"] is False


@pytest.mark.asyncio
async def test_execution_id_is_fresh_and_unique_per_call(isolated_cwd):
    """Two DIFFERENT calls (even identical tool+args) must never share an
    execution_id -- this is what makes the idempotency journal safe to key
    on it directly (see idempotency.py's docstring)."""
    node = make_prepare_execution_node(_settings())
    ai = AIMessage(content="", tool_calls=[
        {"name": "file_read", "args": {"path": "a.txt"}, "id": "c0", "type": "tool_call"},
    ])
    r1 = await node({"messages": [ai]})
    r2 = await node({"messages": [ai]})

    id1 = r1["execution_requests"][0]["request"]["execution_id"]
    id2 = r2["execution_requests"][0]["request"]["execution_id"]
    assert id1 != id2


@pytest.mark.asyncio
async def test_signature_verifies_against_its_own_request(isolated_cwd):
    node = make_prepare_execution_node(_settings())
    ai = _ai_tool_call("file_write", {"path": "a.txt", "content": "hi"})
    result = await node({"messages": [ai]})

    entry = result["execution_requests"][0]
    req = ExecutionRequest(**entry["request"])
    digest = tool_call_fingerprint("file_write", {"path": "a.txt", "content": "hi"})
    ok, reason = approval.verify(req, entry["signature"], current_args_digest=digest)
    assert ok is True and reason == "ok"


@pytest.mark.asyncio
async def test_target_resource_uses_a_recognizable_arg_when_present(isolated_cwd):
    node = make_prepare_execution_node(_settings())
    ai = _ai_tool_call("file_write", {"path": "notes/a.txt", "content": "hi"})
    result = await node({"messages": [ai]})
    assert result["execution_requests"][0]["request"]["target_resource"] == "file_write:notes/a.txt"


@pytest.mark.asyncio
async def test_target_resource_falls_back_to_bare_capability(isolated_cwd):
    node = make_prepare_execution_node(_settings())
    ai = _ai_tool_call("gcp_quota", {})
    result = await node({"messages": [ai]})
    assert result["execution_requests"][0]["request"]["target_resource"] == "gcp_quota"


@pytest.mark.asyncio
async def test_task_contract_status_is_honestly_no_contract(isolated_cwd):
    """Nothing in this repo produces a TaskContract yet (Faz 1 only defined
    the shape) -- must be reported as such, never silently "verified"."""
    node = make_prepare_execution_node(_settings())
    ai = _ai_tool_call("file_read", {"path": "a.txt"})
    result = await node({"messages": [ai]})
    assert result["execution_requests"][0]["request"]["task_contract_status"] == "no_contract"


# ── Tier 2: chained into confirmation_node ──────────────────────────────────

async def _through_pipeline(tool_name, args, settings, *, call_id="call_1"):
    """prepare_execution -> confirmation_node, simulating a resume: callers
    monkeypatch the interrupt call site to RETURN a decision directly (what
    a real Command(resume=...) makes it do) rather than raise, so this
    exercises confirmation_node's post-interrupt code without needing a real
    checkpointed graph run (same monkeypatch target this suite already uses
    for the raise-path in test_confirmation_node.py)."""
    prep = make_prepare_execution_node(settings)
    state = {"messages": [_ai_tool_call(tool_name, args, call_id)], "transport": "cli-text"}
    prep_out = await prep(state)
    state = {**state, **prep_out}
    return state


@pytest.mark.asyncio
async def test_happy_path_approves_and_matches_signed_request(isolated_cwd, monkeypatch):
    settings = _settings()
    state = await _through_pipeline("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}, settings)
    state["user_approved_execution_ids"] = ["approved-in-an-earlier-round"]
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "approve")

    node = make_confirmation_node(settings)
    result = await node(state)

    assert result["confirmation_result"] == "approved"
    expected_id = state["execution_requests"][0]["request"]["execution_id"]
    assert result["user_approved_execution_ids"] == [
        "approved-in-an-earlier-round", expected_id,
    ]


@pytest.mark.asyncio
async def test_interrupt_payload_carries_the_execution_id(isolated_cwd, monkeypatch):
    settings = _settings()
    state = await _through_pipeline("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}, settings)
    expected_id = state["execution_requests"][0]["request"]["execution_id"]

    captured = {}

    def _capture(payload):
        captured.update(payload)
        return "approve"
    monkeypatch.setattr("langgraph.types.interrupt", _capture)

    node = make_confirmation_node(settings)
    await node(state)

    assert captured["tools"][0]["execution_id"] == expected_id


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_decision", ["yes", "", "invalid", "cancel", "approve please", None, 123])
async def test_invalid_decision_is_denied_not_silently_approved(isolated_cwd, monkeypatch, bad_decision):
    """Review remediation: before this fix, anything that wasn't a string
    starting with "deny" fell through to the approve path -- "yes", "",
    a typo, a stray non-string resume value all executed the pending L3
    action. This is the chat-turn gate POST /chat/confirm's unvalidated
    `decision: str` field actually drives, so it must fail closed exactly
    like jarvis.execution.workflow_approval's exact allowlist already does
    for the workflow-engine gate."""
    settings = _settings()
    state = await _through_pipeline("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}, settings)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: bad_decision)

    node = make_confirmation_node(settings)
    result = await node(state)

    # Electron live E2E, 2026-08-09: an explicit interrupt-resume decision
    # (including this fail-closed "wasn't approve, so it's a deny") is now
    # its own confirmation_result value, "user_denied" -- routed straight to
    # the terminal chain with a code-authored answer rather than back through
    # the tool-bound agent (see route_from_confirmation / confirmation_node).
    # The fail-closed BEHAVIOR this test pins is unchanged: nothing executed.
    assert result["confirmation_result"] == "user_denied"
    assert result.get("user_approved_execution_ids", []) == []
    assert any(
        "not executed" in m.content or "not authorized" in m.content
        for m in result["messages"] if hasattr(m, "content")
    )
    assert result["response"].startswith("İşlemi reddettiniz. Komut çalıştırılmadı.")


@pytest.mark.asyncio
async def test_args_changed_after_approval_is_denied(isolated_cwd, monkeypatch):
    """The repair scenario: prepare_execution signed a request for the
    ORIGINAL args, but by the time confirmation_node checks, the pending
    tool_call's args no longer match (simulating a repaired call reusing a
    stale execution_requests entry) -- must be denied, not silently executed
    under the old approval."""
    settings = _settings()
    state = await _through_pipeline("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}, settings)
    # Mutate the pending call's args in place -- the signed request in
    # execution_requests still reflects the ORIGINAL args.
    state["messages"][-1].tool_calls[0]["args"] = {"action": "send", "to": "SOMEONE-ELSE@evil.example"}
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "approve")

    node = make_confirmation_node(settings)
    result = await node(state)

    assert result["confirmation_result"] == "denied"
    assert result.get("user_approved_execution_ids", []) == []
    assert any("no longer valid" in m.content for m in result["messages"] if hasattr(m, "content"))


@pytest.mark.asyncio
async def test_tampered_signature_is_denied(isolated_cwd, monkeypatch):
    settings = _settings()
    state = await _through_pipeline("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}, settings)
    state["execution_requests"][0]["signature"] = "0" * 64
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "approve")

    node = make_confirmation_node(settings)
    result = await node(state)

    assert result["confirmation_result"] == "denied"
    assert result.get("user_approved_execution_ids", []) == []


@pytest.mark.asyncio
async def test_expired_approval_is_denied(isolated_cwd, monkeypatch):
    settings = _settings()
    state = await _through_pipeline("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}, settings)
    entry = state["execution_requests"][0]
    req = ExecutionRequest(**entry["request"])
    expired = req.model_copy(update={
        "expiry": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    })
    entry["request"] = expired.model_dump()
    entry["signature"] = approval.sign(expired)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "approve")

    node = make_confirmation_node(settings)
    result = await node(state)

    assert result["confirmation_result"] == "denied"
    assert result.get("user_approved_execution_ids", []) == []


@pytest.mark.asyncio
async def test_replayed_already_committed_execution_is_denied(isolated_cwd, monkeypatch):
    """The plan's own Faz 2 acceptance test: approve -> (simulate execution
    committing) -> retry the SAME already-approved request -> journal
    refusal."""
    settings = _settings()
    state = await _through_pipeline("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}, settings)
    entry = state["execution_requests"][0]
    exec_id = entry["request"]["execution_id"]
    idempotency.commit(exec_id, "gmail", entry["request"]["normalized_args_digest"])
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "approve")

    node = make_confirmation_node(settings)
    result = await node(state)

    assert result["confirmation_result"] == "denied"
    assert result.get("user_approved_execution_ids", []) == []
    assert any("already ran" in m.content for m in result["messages"] if hasattr(m, "content"))


@pytest.mark.asyncio
async def test_second_approval_of_a_fresh_request_is_not_a_duplicate(isolated_cwd, monkeypatch):
    """Guards against a false positive: a brand new (never-committed)
    execution_id must NOT be denied -- only a genuine journal hit should be."""
    settings = _settings()
    state = await _through_pipeline("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}, settings)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "approve")

    node = make_confirmation_node(settings)
    result = await node(state)

    assert result["confirmation_result"] == "approved"


@pytest.mark.asyncio
async def test_confirmation_node_unaffected_when_execution_requests_absent(isolated_cwd, monkeypatch):
    """Backward compatibility: every pre-Faz-2 test in this suite calls
    confirmation_node directly without ever running prepare_execution --
    approval must still work exactly as before Faz 2 in that case."""
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "approve")
    node = make_confirmation_node(_settings())
    state = {
        "messages": [_ai_tool_call("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"})],
        "transport": "cli-text",
    }

    result = await node(state)

    assert result["confirmation_result"] == "approved"


@pytest.mark.asyncio
async def test_denial_path_is_unaffected_by_faz2_changes(isolated_cwd, monkeypatch):
    settings = _settings()
    state = await _through_pipeline("gmail", {"action": "send", "to": "a@b.c", "subject": "s", "body": "b"}, settings)
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "deny:not now")

    node = make_confirmation_node(settings)
    result = await node(state)

    assert result["confirmation_result"] == "user_denied"
    assert any("not now" in m.content for m in result["messages"] if hasattr(m, "content"))


# ── tool_result_accounting: commits a real success to the journal ─────────

@pytest.mark.asyncio
async def test_tool_result_accounting_commits_successful_call(isolated_cwd):
    settings = _settings()
    prep = make_prepare_execution_node(settings)
    ai = _ai_tool_call("file_write", {"path": "a.txt", "content": "hi"})
    prep_out = await prep({"messages": [ai]})
    exec_id = prep_out["execution_requests"][0]["request"]["execution_id"]

    accounting = make_tool_result_accounting_node(settings)
    messages = [ai, ToolMessage(content="wrote 2 bytes", tool_call_id="call_1")]
    await accounting({"messages": messages, **prep_out})

    assert idempotency.is_committed(exec_id) is True


@pytest.mark.asyncio
async def test_tool_result_accounting_does_not_commit_a_failed_call(isolated_cwd):
    settings = _settings()
    prep = make_prepare_execution_node(settings)
    ai = _ai_tool_call("file_read", {"path": "missing.txt"})
    prep_out = await prep({"messages": [ai]})
    exec_id = prep_out["execution_requests"][0]["request"]["execution_id"]

    accounting = make_tool_result_accounting_node(settings)
    messages = [ai, ToolMessage(content="[ERROR] not found", tool_call_id="call_1")]
    await accounting({"messages": messages, **prep_out})

    assert idempotency.is_committed(exec_id) is False


@pytest.mark.asyncio
async def test_tool_result_accounting_unaffected_when_execution_requests_absent(isolated_cwd):
    """No prepare_execution run -> no crash, no attempted commit."""
    # execution_contract_mode is pinned rather than inherited: this test's
    # subject is the missing-ExecutionRequest path, and its envelope
    # assertion used to read the global default, which Post-MVP Faz 1 moved
    # from "off" to "shadow". A test that silently changes what it asserts
    # when an unrelated default moves is measuring the default, not the
    # behavior (same lesson as the workspace-confinement test that was
    # measuring where the OS puts TEMP).
    settings = _settings(execution_contract_mode="off")
    accounting = make_tool_result_accounting_node(settings)
    ai = _ai_tool_call("file_write", {"path": "a.txt", "content": "hi"})
    messages = [ai, ToolMessage(content="wrote 2 bytes", tool_call_id="call_1")]

    result = await accounting({"messages": messages})

    assert "execution_envelopes" not in result  # explicit "off", the rollback contract
    assert result["completed_tool_fingerprints"]  # pre-existing behavior intact


@pytest.mark.asyncio
async def test_tool_result_accounting_builds_envelopes_under_the_new_default(isolated_cwd):
    """The other half of the pin above: at the shipped default (shadow) the
    same call DOES produce an envelope. Without this, flipping the default
    back to "off" by accident would break nothing in the suite."""
    accounting = make_tool_result_accounting_node(_settings())
    ai = _ai_tool_call("file_write", {"path": "a.txt", "content": "hi"})
    messages = [ai, ToolMessage(content="wrote 2 bytes", tool_call_id="call_1")]

    result = await accounting({"messages": messages})

    assert result["execution_envelopes"], "shadow is the shipped default (jarvis/config.py)"


# ── Tier 3: a real compiled-graph interrupt -> resume round trip ──────────

class _ScriptedLLM:
    def __init__(self, script: list):
        self._script = list(script)
        self.consumed = 0

    def bind_tools(self, *a, **k):
        return self

    def with_fallbacks(self, *a, **k):
        return self

    def bind(self, *a, **k):
        return self

    async def ainvoke(self, messages, **kwargs):
        item = self._script[min(self.consumed, len(self._script) - 1)]
        self.consumed += 1
        return item if isinstance(item, AIMessage) else AIMessage(content=str(item))


def _ai_tool(name: str, args: dict, call_id: str = "call_0") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


ACCEPT = '{"verdict": "accept", "critique": "", "score": 9}'


async def _invoke_and_get_interrupt(graph, state, config):
    """Handle both LangGraph interrupt surfaces (raised GraphInterrupt vs.
    result["__interrupt__"]) -- see agent.py's own comment on why both exist
    on this LangGraph version."""
    try:
        result = await graph.ainvoke(state, config)
    except GraphInterrupt as exc:
        return exc.args[0][0].value, None
    pending = result.get("__interrupt__") or []
    if pending:
        return pending[0].value, result
    return None, result


# ── Completion Contract Source Binding (Pr_2 section 6): the pre-execution
# guard. Detection lives in prepare_execution_node (this tier), blocking in
# confirmation_node (tier 2 style, chained the same way as the rest of this
# file) -- see jarvis.execution.output_contract.SOURCE_MISMATCH_BLOCK_OUTCOME.

def _source_bound_state(
    tool_name: str, args: dict, *, required_source: str = "satis.csv", call_id: str = "call_1",
) -> dict:
    return {
        "messages": [_ai_tool_call(tool_name, args, call_id)],
        "transport": "cli-text",
        "required_outputs": [{
            "kind": "chart", "operation": "create",
            "source": {"type": "file", "raw": required_source, "basename": required_source},
        }],
    }


@pytest.mark.asyncio
async def test_a_mismatched_plot_data_call_is_flagged_in_enforce_mode(isolated_cwd):
    settings = _settings(required_outputs_mode="enforce")
    state = _source_bound_state("plot_data", {"path": "other.csv"})

    result = await make_prepare_execution_node(settings)(state)

    assert len(result["source_mismatch_calls"]) == 1
    entry = result["source_mismatch_calls"][0]
    assert entry["tool_call_id"] == "call_1"
    assert entry["capability"] == "plot_data"
    assert entry["requested_label"] == "satis.csv"
    assert entry["actual_label"] == "other.csv"
    assert result["execution_requests"] == [], \
        "no ExecutionRequest should be minted for a call that will be blocked anyway"


@pytest.mark.asyncio
async def test_a_matching_plot_data_call_is_not_flagged(isolated_cwd):
    settings = _settings(required_outputs_mode="enforce")
    state = _source_bound_state("plot_data", {"path": "satis.csv"})

    result = await make_prepare_execution_node(settings)(state)

    assert result["source_mismatch_calls"] == []
    assert len(result["execution_requests"]) == 1


@pytest.mark.asyncio
async def test_a_bare_filename_request_is_not_flagged_when_the_call_uses_a_subdirectory(
    isolated_cwd, tmp_path,
):
    """Regression for the false positive a live qwen3:8b smoke run caught
    (Pr_2 section 10): asked for a bare 'satis.csv', the model reasonably
    called plot_data(path='Desktop/satis.csv') -- that must NOT be flagged,
    since the user never named a directory."""
    (tmp_path / "Desktop").mkdir()
    (tmp_path / "Desktop" / "satis.csv").write_text("x", encoding="utf-8")
    settings = _settings(required_outputs_mode="enforce")
    state = _source_bound_state("plot_data", {"path": "Desktop/satis.csv"})

    result = await make_prepare_execution_node(settings, workspace=tmp_path)(state)

    assert result["source_mismatch_calls"] == []


@pytest.mark.asyncio
async def test_no_source_argument_at_all_is_caught_earlier_as_invalid_args(isolated_cwd):
    """plot_data with neither `path` nor `data_json` never reaches the source
    check at all: PlotDataArgs' own schema (args_schemas.py) already requires
    exactly one of them, so this is MISSING_INVALID_ARGS territory, caught by
    the more fundamental, pre-existing gate -- not silently waved through as
    a source match, and not double-classified as a source mismatch either."""
    settings = _settings(required_outputs_mode="enforce")
    state = _source_bound_state("plot_data", {})

    result = await make_prepare_execution_node(settings)(state)

    assert result["source_mismatch_calls"] == []
    assert len(result["invalid_args_calls"]) == 1
    assert result["invalid_args_calls"][0]["capability"] == "plot_data"


@pytest.mark.asyncio
async def test_inline_data_json_is_flagged_against_a_named_file_requirement(isolated_cwd):
    settings = _settings(required_outputs_mode="enforce")
    state = _source_bound_state("plot_data", {"data_json": "[1,2,3]"})

    result = await make_prepare_execution_node(settings)(state)

    assert len(result["source_mismatch_calls"]) == 1
    assert result["source_mismatch_calls"][0]["actual_label"] == "inline data"


@pytest.mark.asyncio
async def test_shadow_mode_never_flags_a_mismatch(isolated_cwd):
    """Shadow must not block: this list is populated ONLY in enforce, so
    confirmation_node's pre-gate is a no-op in shadow for the same call."""
    settings = _settings(required_outputs_mode="shadow")
    state = _source_bound_state("plot_data", {"path": "other.csv"})

    result = await make_prepare_execution_node(settings)(state)

    assert result["source_mismatch_calls"] == []


@pytest.mark.asyncio
async def test_off_mode_never_flags_a_mismatch(isolated_cwd):
    settings = _settings(required_outputs_mode="off")
    state = _source_bound_state("plot_data", {"path": "other.csv"})

    result = await make_prepare_execution_node(settings)(state)

    assert result["source_mismatch_calls"] == []


@pytest.mark.asyncio
async def test_an_unbound_requirement_never_flags_a_mismatch(isolated_cwd):
    """No `source` key on the requirement -- a generic "bu dosya" request.
    Nothing was named, so nothing is ever compared against."""
    settings = _settings(required_outputs_mode="enforce")
    state = {
        "messages": [_ai_tool_call("plot_data", {"path": "whatever.csv"})],
        "transport": "cli-text",
        "required_outputs": [{"kind": "chart", "operation": "create"}],
    }

    result = await make_prepare_execution_node(settings)(state)

    assert result["source_mismatch_calls"] == []


@pytest.mark.asyncio
async def test_a_non_producer_tool_call_is_never_flagged(isolated_cwd):
    """file_list is not a chart producer -- only tools_producing(kind,
    operation) are checked, even under an enforced, source-bound requirement."""
    settings = _settings(required_outputs_mode="enforce")
    state = _source_bound_state("file_list", {"path": "."})

    result = await make_prepare_execution_node(settings)(state)

    assert result["source_mismatch_calls"] == []


@pytest.mark.asyncio
async def test_confirmation_node_blocks_a_flagged_call_before_the_interrupt(isolated_cwd, monkeypatch):
    """The whole point: a mismatched producer call must never reach the real
    confirmation interrupt (which a human might simply click through) -- it
    is refused structurally, before that."""
    def _fail_if_reached(payload):
        raise AssertionError("must not reach the interrupt for a source-mismatched call")
    monkeypatch.setattr("langgraph.types.interrupt", _fail_if_reached)

    settings = _settings(required_outputs_mode="enforce")
    prep_state = _source_bound_state("plot_data", {"path": "other.csv"})
    prep_out = await make_prepare_execution_node(settings)(prep_state)
    state = {**prep_state, **prep_out}

    result = await make_confirmation_node(settings)(state)

    assert result["confirmation_result"] == "denied"
    assert any("BLOCKED_OUTPUT_SOURCE_MISMATCH" in m.content
               for m in result["messages"] if hasattr(m, "content"))
    outcomes = {e["outcome"] for e in result["preexecution_history"]}
    assert "blocked_output_source_mismatch" in outcomes


@pytest.mark.asyncio
async def test_a_blocked_source_mismatch_routes_back_to_the_agent_not_end(isolated_cwd):
    """Same routing as every other structural pre-gate denial -- the model
    gets the stub + ack back and decides what to say; it is NOT composed
    into a final answer here (that is output_contract's job once the turn
    reaches the terminal chain, not confirmation_node's)."""
    from jarvis.graph.nodes import route_from_confirmation

    settings = _settings(required_outputs_mode="enforce")
    prep_state = _source_bound_state("plot_data", {"path": "other.csv"})
    prep_out = await make_prepare_execution_node(settings)(prep_state)
    state = {**prep_state, **prep_out}

    result = await make_confirmation_node(settings)(state)

    assert route_from_confirmation({**state, **result}) == "agent"


@pytest.mark.asyncio
async def test_a_correctly_sourced_call_is_not_blocked_by_this_guard(isolated_cwd, monkeypatch):
    """Mirror case: a matching call proceeds to the ordinary policy gate
    exactly as before Source Binding existed."""
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "approve")
    settings = _settings(required_outputs_mode="enforce")
    prep_state = _source_bound_state("plot_data", {"path": "satis.csv"})
    prep_out = await make_prepare_execution_node(settings)(prep_state)
    state = {**prep_state, **prep_out}

    result = await make_confirmation_node(settings)(state)

    assert result["confirmation_result"] == "approved"


@pytest.mark.asyncio
async def test_36_a_source_mismatch_block_never_spends_the_shared_repair_budget(isolated_cwd):
    """Rollout parity 36. The shared corrective-repair budget
    (jarvis.graph.repair_budget) is for completion repairs, invalid-args
    repairs and the claim gate -- a structural source-mismatch block is none
    of those and must not consume it, or a turn's legitimate ONE repair
    would already be spent by a block this guard, not a repair, caused."""
    from jarvis.graph.repair_budget import corrective_repair_spent

    settings = _settings(required_outputs_mode="enforce")
    prep_state = _source_bound_state("plot_data", {"path": "other.csv"})
    prep_out = await make_prepare_execution_node(settings)(prep_state)
    state = {**prep_state, **prep_out}

    confirm_out = await make_confirmation_node(settings)(state)

    assert "repair_attempts_total" not in confirm_out
    assert "repair_reason" not in confirm_out
    merged = {**state, **confirm_out}
    assert corrective_repair_spent(merged, settings) is False


@pytest.mark.asyncio
async def test_a_mismatched_call_blocks_its_whole_batch_including_a_correct_sibling(isolated_cwd):
    """Two plot_data calls in ONE round: this graph has no partial-batch
    dispatch, so the correct sibling is blocked too THIS round -- the same
    'whole batch rejected wholesale' precedent every other pre-gate in this
    node already follows (batch limit, duplicates, invalid args). It is free
    to retry the correct one alone next round -- section 8's 'at least one
    source-correct call can still satisfy the requirement' is about the
    TURN, not about salvaging part of one blocked batch."""
    settings = _settings(required_outputs_mode="enforce")
    ai = AIMessage(content="", tool_calls=[
        {"name": "plot_data", "args": {"path": "other.csv"}, "id": "bad", "type": "tool_call"},
        {"name": "plot_data", "args": {"path": "satis.csv"}, "id": "good", "type": "tool_call"},
    ])
    state = {
        "messages": [ai], "transport": "cli-text",
        "required_outputs": [{"kind": "chart", "operation": "create",
                              "source": {"type": "file", "raw": "satis.csv", "basename": "satis.csv"}}],
    }
    prep_out = await make_prepare_execution_node(settings)(state)
    state = {**state, **prep_out}

    result = await make_confirmation_node(settings)(state)

    assert result["confirmation_result"] == "denied"
    ids_blocked = {e["tool_call_id"] for e in result["preexecution_history"]}
    assert ids_blocked == {"bad", "good"}


@pytest.mark.asyncio
async def test_real_graph_interrupt_then_approve_executes_and_commits(isolated_cwd, tmp_path, monkeypatch):
    from jarvis.graph.graph import build_graph, make_checkpointer
    from jarvis.memory import Memory
    from langgraph.types import Command

    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = Settings(_env_file=None)
    llm = _ScriptedLLM([
        _ai_tool("shell_run", {"command": "echo ok"}),
        "Komut calistirildi.",
        ACCEPT,
    ])
    monkeypatch.setattr("jarvis.providers.get_llm", lambda *a, **k: llm)
    monkeypatch.setattr("jarvis.graph.graph.get_llm", lambda *a, **k: llm)

    memory = Memory(settings)
    checkpointer = make_checkpointer(workspace / "cp" / "checkpoints.db")
    graph = build_graph(settings, workspace, memory, checkpointer=checkpointer)

    state = {
        "messages": [SystemMessage(content="test"), HumanMessage(content="shell ile echo ok calistir")],
        "user_query": "shell ile echo ok calistir",
        "language": "tr", "memory_context": "", "needs_planning": False,
        "use_pro_agent": False, "plan": "", "response": "", "revise_count": 0,
        "critic_verdict": "", "critique": "", "transport": "cli-text",
        "tool_route": None, "tool_calls_attempted": 0, "tool_rounds": 0,
        "seen_tool_fingerprints": [], "completed_tool_fingerprints": [],
        "tool_execution_ledger": [],
    }
    config = {"configurable": {"thread_id": "prep-exec-real-graph"}, "recursion_limit": 25}

    payload, _ = await _invoke_and_get_interrupt(graph, state, config)
    assert payload is not None, "shell_run must interrupt for confirmation"
    assert payload["tools"][0]["name"] == "shell_run"
    exec_id = payload["tools"][0]["execution_id"]
    assert exec_id, "Faz 2: the interrupt payload must name the ExecutionRequest's execution_id"
    assert idempotency.is_committed(exec_id) is False  # not executed yet

    result = await graph.ainvoke(Command(resume="approve"), config)

    assert "__interrupt__" not in (result or {})
    assert idempotency.is_committed(exec_id) is True, "a real successful execution must commit to the journal"


# ── Electron live E2E, 2026-08-09: explicit user denial is code-authored ──
#
# The live round-trip proved the mechanism (card, exactly-one-execution on
# approve, zero on deny, no raw protocol leak) but caught a real bug: an
# explicit human DENY was still routed back through the tool-bound agent
# purely to have the model acknowledge a fact confirmation_node already knew
# with certainty. The model correctly said the action was denied, then
# fabricated an unrelated cause ("permission restrictions... run PowerShell
# as an administrator") for a denial that was the user's own choice.
# confirmation_node now composes the final answer itself for this one
# outcome and routes straight to the terminal chain (verify, and
# output_contract when enabled) -- the exact same choke-point shape
# invalid_args_exhausted already used. The tests below are against the REAL
# compiled graph, not a direct node call, so they exercise route_from_
# confirmation's actual conditional edge, not just its return value.

@pytest.mark.asyncio
async def test_real_graph_interrupt_then_deny_never_calls_the_agent_again(
    isolated_cwd, tmp_path, monkeypatch,
):
    """Falsifiable the way this bug actually was: give the scripted LLM a
    SECOND response that would only ever be consumed if the graph called it
    again after the deny. If that text leaked into the final answer, or if
    the LLM was invoked a second time at all, this fails."""
    from jarvis.graph.graph import build_graph, make_checkpointer
    from jarvis.memory import Memory
    from langgraph.types import Command

    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = Settings(_env_file=None)
    poison = "HALLUCINATED_POST_DENY_NARRATION_MUST_NEVER_APPEAR"
    llm = _ScriptedLLM([
        _ai_tool("shell_run", {"command": "echo ok"}),
        AIMessage(content=poison),
    ])
    monkeypatch.setattr("jarvis.providers.get_llm", lambda *a, **k: llm)
    monkeypatch.setattr("jarvis.graph.graph.get_llm", lambda *a, **k: llm)

    memory = Memory(settings)
    checkpointer = make_checkpointer(workspace / "cp" / "checkpoints.db")
    graph = build_graph(settings, workspace, memory, checkpointer=checkpointer)

    state = {
        "messages": [SystemMessage(content="test"), HumanMessage(content="shell ile echo ok calistir")],
        "user_query": "shell ile echo ok calistir",
        "language": "tr", "memory_context": "", "needs_planning": False,
        "use_pro_agent": False, "plan": "", "response": "", "revise_count": 0,
        "critic_verdict": "", "critique": "", "transport": "cli-text",
        "tool_route": None, "tool_calls_attempted": 0, "tool_rounds": 0,
        "seen_tool_fingerprints": [], "completed_tool_fingerprints": [],
        "tool_execution_ledger": [],
    }
    config = {"configurable": {"thread_id": "prep-exec-real-graph-deny"}, "recursion_limit": 25}

    payload, _ = await _invoke_and_get_interrupt(graph, state, config)
    assert payload is not None, "shell_run must interrupt for confirmation"
    exec_id = payload["tools"][0]["execution_id"]
    assert idempotency.is_committed(exec_id) is False
    assert llm.consumed == 1, "sanity: exactly one LLM call before the interrupt"

    result = await graph.ainvoke(Command(resume="deny"), config)

    assert "__interrupt__" not in (result or {})
    assert idempotency.is_committed(exec_id) is False, "a denied action must never execute"
    assert llm.consumed == 1, (
        "the agent LLM must NOT be invoked again after an explicit user deny -- "
        "confirmation_node must compose the final answer itself"
    )
    assert result["confirmation_result"] == "user_denied"
    assert result["response"] == "İşlemi reddettiniz. Komut çalıştırılmadı."
    assert poison not in result["response"]
    assert not any(
        isinstance(m, AIMessage) and poison in (m.content or "")
        for m in result["messages"]
    )


@pytest.mark.asyncio
async def test_real_graph_interrupt_then_deny_with_reason_is_quoted_not_reinterpreted(
    isolated_cwd, tmp_path, monkeypatch,
):
    """A user-supplied deny reason is carried through verbatim -- the code
    never invents or reinterprets a cause, matching 'do not invent causes'."""
    from jarvis.graph.graph import build_graph, make_checkpointer
    from jarvis.memory import Memory
    from langgraph.types import Command

    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = Settings(_env_file=None)
    llm = _ScriptedLLM([_ai_tool("shell_run", {"command": "echo ok"})])
    monkeypatch.setattr("jarvis.providers.get_llm", lambda *a, **k: llm)
    monkeypatch.setattr("jarvis.graph.graph.get_llm", lambda *a, **k: llm)

    memory = Memory(settings)
    checkpointer = make_checkpointer(workspace / "cp" / "checkpoints.db")
    graph = build_graph(settings, workspace, memory, checkpointer=checkpointer)

    state = {
        "messages": [SystemMessage(content="test"), HumanMessage(content="shell ile echo ok calistir")],
        "user_query": "shell ile echo ok calistir",
        "language": "tr", "memory_context": "", "needs_planning": False,
        "use_pro_agent": False, "plan": "", "response": "", "revise_count": 0,
        "critic_verdict": "", "critique": "", "transport": "cli-text",
        "tool_route": None, "tool_calls_attempted": 0, "tool_rounds": 0,
        "seen_tool_fingerprints": [], "completed_tool_fingerprints": [],
        "tool_execution_ledger": [],
    }
    config = {"configurable": {"thread_id": "prep-exec-real-graph-deny-reason"}, "recursion_limit": 25}

    await _invoke_and_get_interrupt(graph, state, config)
    result = await graph.ainvoke(Command(resume="deny:henuz emin degilim"), config)

    assert result["response"] == (
        "İşlemi reddettiniz. Komut çalıştırılmadı. Belirttiğiniz gerekçe: henuz emin degilim"
    )


@pytest.mark.asyncio
async def test_real_graph_interrupt_then_deny_passes_through_output_contract_when_enabled(
    isolated_cwd, tmp_path, monkeypatch,
):
    """The terminal honesty choke point must not be bypassable: with
    required_outputs_mode on, user_denied routes through output_contract
    (this turn declares no required output, so it's a pass-through) and then
    verify, exactly like every other terminal path -- never straight to a
    raw graph END."""
    from jarvis.graph.graph import build_graph, make_checkpointer
    from jarvis.memory import Memory
    from langgraph.types import Command

    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = Settings(_env_file=None, required_outputs_mode="shadow")
    llm = _ScriptedLLM([_ai_tool("shell_run", {"command": "echo ok"})])
    monkeypatch.setattr("jarvis.providers.get_llm", lambda *a, **k: llm)
    monkeypatch.setattr("jarvis.graph.graph.get_llm", lambda *a, **k: llm)

    memory = Memory(settings)
    checkpointer = make_checkpointer(workspace / "cp" / "checkpoints.db")
    graph = build_graph(settings, workspace, memory, checkpointer=checkpointer)

    state = {
        "messages": [SystemMessage(content="test"), HumanMessage(content="shell ile echo ok calistir")],
        "user_query": "shell ile echo ok calistir",
        "language": "tr", "memory_context": "", "needs_planning": False,
        "use_pro_agent": False, "plan": "", "response": "", "revise_count": 0,
        "critic_verdict": "", "critique": "", "transport": "cli-text",
        "tool_route": None, "tool_calls_attempted": 0, "tool_rounds": 0,
        "seen_tool_fingerprints": [], "completed_tool_fingerprints": [],
        "tool_execution_ledger": [],
    }
    config = {"configurable": {"thread_id": "prep-exec-real-graph-deny-contract"}, "recursion_limit": 25}

    await _invoke_and_get_interrupt(graph, state, config)
    result = await graph.ainvoke(Command(resume="deny"), config)

    assert "__interrupt__" not in (result or {})
    assert result["response"] == "İşlemi reddettiniz. Komut çalıştırılmadı."
    assert result["output_contract_action"] == "continue", (
        "output_contract must actually run this turn (mode is shadow, not off) "
        "and pass the code-authored deny answer straight through"
    )


def test_route_from_confirmation_full_branch_table():
    """Direct, cheap documentation of every branch -- catches a future
    accidental change to any one of them without needing a real graph."""
    from jarvis.graph.nodes import route_from_confirmation
    from langgraph.graph import END

    assert route_from_confirmation({"confirmation_result": "approved"}) == "tools"
    assert route_from_confirmation({}) == "tools", "missing key defaults to approved -> tools"
    assert route_from_confirmation({"confirmation_result": "user_denied"}) == END
    assert route_from_confirmation({"confirmation_result": "invalid_args_exhausted"}) == END
    # Every OTHER denied-family outcome (kill switch, capability disabled,
    # external writes off, proactive read-only, stale approval, duplicate
    # execution) still needs the model to narrate its own specific reason --
    # this is the one branch this fix deliberately left unchanged.
    assert route_from_confirmation({"confirmation_result": "denied"}) == "agent"
