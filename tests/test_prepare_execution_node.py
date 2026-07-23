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
    monkeypatch.setattr("langgraph.types.interrupt", lambda payload: "approve")

    node = make_confirmation_node(settings)
    result = await node(state)

    assert result["confirmation_result"] == "approved"


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

    assert result["confirmation_result"] == "denied"
    assert any(
        "not executed" in m.content or "not authorized" in m.content
        for m in result["messages"] if hasattr(m, "content")
    )


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

    assert result["confirmation_result"] == "denied"
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
    settings = _settings()
    accounting = make_tool_result_accounting_node(settings)
    ai = _ai_tool_call("file_write", {"path": "a.txt", "content": "hi"})
    messages = [ai, ToolMessage(content="wrote 2 bytes", tool_call_id="call_1")]

    result = await accounting({"messages": messages})

    assert "execution_envelopes" not in result  # off mode, unrelated to this change
    assert result["completed_tool_fingerprints"]  # pre-existing behavior intact


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
