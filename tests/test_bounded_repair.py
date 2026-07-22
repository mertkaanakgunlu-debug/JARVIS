"""Agent Runtime rev.2, Faz 6 Part 2 -- bounded repair for invalid tool-call
arguments.

Three tiers, mirroring test_prepare_execution_node.py's own shape:
  1. prepare_execution_node in isolation -- what invalid_args_calls it builds,
     and that raw args are never substituted (the digest/execution
     divergence risk an external review caught, closed by construction).
  2. confirmation_node chained after it -- the whole-batch reject + explicit
     one-repair-then-exhausted state machine, and route_from_confirmation's
     three branches.
  3. One real compiled-graph run where a scripted model sends the same
     invalid gmail call twice, proving the graph actually terminates with an
     honest answer instead of looping or crashing.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from jarvis.config import Settings
from jarvis.graph.nodes import (
    make_confirmation_node,
    make_prepare_execution_node,
    route_from_confirmation,
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, confirmation_gate_enabled=True, **overrides)


def _ai_tool_call(tool_name: str, args: dict | None = None, call_id: str = "call_1") -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": tool_name, "args": args or {}, "id": call_id, "type": "tool_call"}],
    )


# ── Tier 1: prepare_execution_node's validation gate ────────────────────────

@pytest.mark.asyncio
async def test_invalid_call_produces_no_execution_request(isolated_cwd):
    node = make_prepare_execution_node(_settings())
    ai = _ai_tool_call("gmail", {"action": "send", "to": "a@b.c"})  # missing subject/body
    result = await node({"messages": [ai]})

    assert result["execution_requests"] == []
    assert len(result["invalid_args_calls"]) == 1
    entry = result["invalid_args_calls"][0]
    assert entry["tool_call_id"] == "call_1"
    assert entry["capability"] == "gmail"
    assert entry["errors"]


@pytest.mark.asyncio
async def test_valid_call_is_unaffected_when_no_schema_registered(isolated_cwd):
    """file_write has no args_schema (Part 1's own honest scope boundary) --
    must sail through exactly as before, invalid_args_calls stays empty."""
    node = make_prepare_execution_node(_settings())
    ai = _ai_tool_call("file_write", {"path": "a.txt", "content": "hi"})
    result = await node({"messages": [ai]})

    assert len(result["execution_requests"]) == 1
    assert result["invalid_args_calls"] == []


@pytest.mark.asyncio
async def test_mixed_batch_only_the_invalid_call_is_flagged(isolated_cwd):
    ai = AIMessage(content="", tool_calls=[
        {"name": "file_write", "args": {"path": "a.txt", "content": "hi"}, "id": "ok", "type": "tool_call"},
        {"name": "gmail", "args": {"action": "send", "to": "a@b.c"}, "id": "bad", "type": "tool_call"},
    ])
    node = make_prepare_execution_node(_settings())
    result = await node({"messages": [ai]})

    assert {r["tool_call_id"] for r in result["execution_requests"]} == {"ok"}
    assert {c["tool_call_id"] for c in result["invalid_args_calls"]} == {"bad"}


@pytest.mark.asyncio
async def test_raw_args_are_never_substituted_by_normalization(isolated_cwd):
    """The digest/execution divergence risk: a call that only VALIDATES
    thanks to the schema's internal .strip().lower() normalizer must still
    be fingerprinted/target-resolved from the ORIGINAL raw args, not a
    canonicalized copy -- validate_args() is a pure gate, never a
    transform (see args_schemas.py's own docstring)."""
    from jarvis.graph.tool_accounting import tool_call_fingerprint

    node = make_prepare_execution_node(_settings())
    raw_args = {"action": " PLAY "}  # no "query" -- _resolve_target_resource has no arg to key on
    ai = _ai_tool_call("spotify", raw_args)
    result = await node({"messages": [ai]})

    assert result["invalid_args_calls"] == []  # normalizer accepts it
    entry = result["execution_requests"][0]["request"]
    assert entry["normalized_args_digest"] == tool_call_fingerprint("spotify", raw_args)
    # falls back to the bare capability -- proves target_resource was
    # resolved from the RAW args (with the untrimmed "action" key, which
    # _resolve_target_resource doesn't even look at), not a canonicalized
    # copy where a normalizer might have touched something it keys on.
    assert entry["target_resource"] == "spotify"


# ── Tier 2: confirmation_node's whole-batch reject + explicit repair state ──

async def _through_pipeline(tool_name, args, settings, *, invalid_state=None, call_id="call_1"):
    prep = make_prepare_execution_node(settings)
    state = {
        "messages": [_ai_tool_call(tool_name, args, call_id)],
        "transport": "cli-text",
        **(invalid_state or {}),
    }
    prep_out = await prep(state)
    return {**state, **prep_out}


@pytest.mark.asyncio
async def test_first_invalid_batch_rejects_whole_batch_and_grants_one_repair(isolated_cwd):
    settings = _settings()
    state = await _through_pipeline("gmail", {"action": "send", "to": "a@b.c"}, settings)

    node = make_confirmation_node(settings)
    result = await node(state)

    assert result["confirmation_result"] == "denied"
    assert result["args_repair_attempted"] is True
    contents = [m.content for m in result["messages"]]
    assert any(c.startswith("[INVALID_ARGS:") for c in contents)
    assert route_from_confirmation({**state, **result}) == "agent"


@pytest.mark.asyncio
async def test_mixed_batch_reject_skips_the_individually_valid_call_too():
    """Whole-batch reject: the valid call in the same batch must NOT execute
    either -- same 'don't guess which part was safe' precedent as every
    other pre-gate rejection in confirmation_node."""
    from jarvis.graph.nodes import make_prepare_execution_node as _mk

    settings = _settings()
    ai = AIMessage(content="", tool_calls=[
        {"name": "file_write", "args": {"path": "a.txt", "content": "hi"}, "id": "ok", "type": "tool_call"},
        {"name": "gmail", "args": {"action": "send", "to": "a@b.c"}, "id": "bad", "type": "tool_call"},
    ])
    prep_out = await _mk(settings)({"messages": [ai], "transport": "cli-text"})
    state = {"messages": [ai], "transport": "cli-text", **prep_out}

    result = await make_confirmation_node(settings)(state)

    assert result["confirmation_result"] == "denied"
    by_id = {}
    for m in result["messages"]:
        call_id = getattr(m, "tool_call_id", None)
        if call_id:
            by_id[call_id] = m.content
    assert by_id["bad"].startswith("[INVALID_ARGS:")
    assert "SKIPPED" in by_id["ok"]


@pytest.mark.asyncio
async def test_second_invalid_batch_in_same_turn_is_exhausted(isolated_cwd):
    settings = _settings()
    state = await _through_pipeline(
        "gmail", {"action": "send", "to": "a@b.c"}, settings,
        invalid_state={"args_repair_attempted": True},  # repair already used this turn
    )

    result = await make_confirmation_node(settings)(state)

    assert result["confirmation_result"] == "invalid_args_exhausted"
    assert result["response"]  # a real, non-empty honest answer
    assert "gmail" in result["response"]
    assert len(result["messages"]) == 1 and isinstance(result["messages"][0], AIMessage)
    assert route_from_confirmation({**state, **result}) is not None


@pytest.mark.asyncio
async def test_valid_batch_after_a_prior_repair_flag_proceeds_normally(isolated_cwd):
    """args_repair_attempted alone must not block a batch that is actually
    valid -- it only matters when invalid_args_calls is non-empty."""
    settings = _settings()
    state = await _through_pipeline(
        "file_write", {"path": "a.txt", "content": "hi"}, settings,
        invalid_state={"args_repair_attempted": True},
    )
    result = await make_confirmation_node(settings)(state)
    assert result["confirmation_result"] == "approved"


def test_route_from_confirmation_all_three_branches():
    from langgraph.graph import END
    assert route_from_confirmation({"confirmation_result": "approved"}) == "tools"
    assert route_from_confirmation({"confirmation_result": "denied"}) == "agent"
    assert route_from_confirmation({"confirmation_result": "invalid_args_exhausted"}) == END


# ── Tier 3: real compiled graph, scripted model repeats the same mistake ────

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


@pytest.mark.asyncio
async def test_real_graph_terminates_honestly_after_two_invalid_attempts(isolated_cwd, tmp_path, monkeypatch):
    """A model that fails to correct itself even after seeing exactly what
    was wrong must not loop forever or crash -- the graph must reach END
    with a real, non-empty answer after exactly one bounded repair."""
    from jarvis.graph.graph import build_graph, make_checkpointer
    from jarvis.memory import Memory

    workspace = tmp_path / "ws"
    workspace.mkdir()
    settings = Settings(_env_file=None)
    # Same invalid call twice -- the fake model never "learns" from the
    # rejection, which is exactly the case bounded repair must survive.
    llm = _ScriptedLLM([
        _ai_tool("gmail", {"action": "send", "to": "a@b.c"}),
        _ai_tool("gmail", {"action": "send", "to": "a@b.c"}, call_id="call_1"),
    ])
    monkeypatch.setattr("jarvis.providers.get_llm", lambda *a, **k: llm)
    monkeypatch.setattr("jarvis.graph.graph.get_llm", lambda *a, **k: llm)

    memory = Memory(settings)
    checkpointer = make_checkpointer(workspace / "cp" / "checkpoints.db")
    graph = build_graph(settings, workspace, memory, checkpointer=checkpointer)

    state = {
        "messages": [SystemMessage(content="test"), HumanMessage(content="mail gonder")],
        "user_query": "mail gonder",
        "language": "tr", "memory_context": "", "needs_planning": False,
        "use_pro_agent": False, "plan": "", "response": "", "revise_count": 0,
        "critic_verdict": "", "critique": "", "transport": "cli-text",
        "tool_route": None, "tool_calls_attempted": 0, "tool_rounds": 0,
        "seen_tool_fingerprints": [], "completed_tool_fingerprints": [],
        "tool_execution_ledger": [], "args_repair_attempted": False,
    }
    config = {"configurable": {"thread_id": "bounded-repair-real-graph"}, "recursion_limit": 25}

    result = await graph.ainvoke(state, config)

    assert "__interrupt__" not in (result or {})  # never reached confirmation for a VALID call
    assert result.get("confirmation_result") == "invalid_args_exhausted"
    assert result.get("response"), "must end with a real, non-empty honest answer"
    assert llm.consumed == 2, "must have retried exactly once, not looped or given up early"


# ── Tier 4: JarvisAgent.chat()-level integration ────────────────────────────
# Follows tests/test_interrupt_surface.py's established pattern: JarvisAgent.
# __new__() skips __init__ (constructing a real agent is heavy), driven with
# a minimal set of collaborators standing in for exactly what chat() touches.

def _base_agent(jarvis_home, *, starting_turn: int = 0):
    from jarvis.agent import JarvisAgent

    agent = JarvisAgent.__new__(JarvisAgent)
    agent._pending_confirmations = {}
    agent._last_turn_used_pro = False
    agent._turn = starting_turn
    agent.session_id = "s1"
    agent._history = []
    agent.workspace = jarvis_home
    agent._env_block = ""
    agent.usage = SimpleNamespace()
    agent.settings = Settings(_env_file=None)
    agent._last_turn_trace = None
    agent._active_model_id = None
    agent._effective_settings = agent.settings

    async def _noop_lock():
        return None
    agent._acquire_state_lock = _noop_lock
    agent._state_lock = SimpleNamespace(release=lambda: None)

    async def _connect():
        return None
    agent.connect_mcp_tools = _connect
    agent._context_builder = SimpleNamespace(
        build=lambda *a, **k: SimpleNamespace(
            memory_ctx="", entities_block="", past_sessions_block="",
            open_todos_block="", facts_block="", procedure_block="",
        )
    )
    agent.session_store = SimpleNamespace(
        save_turn=lambda *a, **k: None, set_topic_hint=lambda *a, **k: None,
    )
    agent.memory = SimpleNamespace(store=lambda *a, **k: None, log_turn=lambda *a, **k: None)
    agent._schedule_memory_extraction = lambda *a, **k: None
    return agent


@pytest.mark.asyncio
async def test_chat_returns_the_exhausted_paths_honest_answer(jarvis_home):
    """invalid_args_exhausted's composed answer must actually reach the
    caller of chat() as a real, non-empty, non-echoed response -- not just
    live correctly inside confirmation_node's own return dict."""
    agent = _base_agent(jarvis_home)
    honest_text = "I couldn't complete this -- the arguments were still invalid after one corrected attempt:\n- gmail: requires 'subject'"

    class _Graph:
        async def ainvoke(self, state, config):
            return {
                "confirmation_result": "invalid_args_exhausted",
                "response": honest_text,
                "messages": list(state["messages"]) + [AIMessage(content=honest_text)],
            }
    agent._graph = _Graph()

    response, _ = await agent.chat("mail gonder", transport="api")

    assert response == honest_text
    assert response.strip() != ""


@pytest.mark.asyncio
async def test_args_repair_attempted_resets_on_the_next_turn(jarvis_home, monkeypatch):
    """Each turn gets a fresh thread_id (f'{session_id}-t{turn}') and a
    hand-built initial state dict -- args_repair_attempted must be an
    explicit False in THAT construction every time, never inherited from a
    previous turn's graph state."""
    agent = _base_agent(jarvis_home)
    captured_states = []

    class _Graph:
        async def ainvoke(self, state, config):
            captured_states.append(dict(state))
            return {"response": "ok", "messages": list(state["messages"]) + [AIMessage(content="ok")]}
    agent._graph = _Graph()

    await agent.chat("ilk mesaj", transport="api")
    await agent.chat("ikinci mesaj", transport="api")

    assert len(captured_states) == 2
    assert captured_states[0]["args_repair_attempted"] is False
    assert captured_states[1]["args_repair_attempted"] is False
