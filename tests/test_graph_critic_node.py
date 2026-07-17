"""jarvis/graph/nodes.py -- critic_node's empty-response handling.

Covers BUG-emptyresp: the critic's "fast path" used to fire on `not
response_text OR revise_count >= 2`, unconditionally returning verdict=accept
for an empty response on ANY revise_count -- silently ending the turn with
nothing shown to the user instead of giving the agent a chance to retry.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from jarvis.graph.nodes import make_critic_node, route_from_critic


async def _run(state: dict) -> dict:
    critic = make_critic_node(llm_pro=None)  # unused on the fast paths under test
    return await critic(state)


async def test_empty_response_with_budget_remaining_redirects_for_a_retry():
    state = {
        "messages": [HumanMessage(content="hello"), AIMessage(content="")],
        "revise_count": 0,
    }
    out = await _run(state)

    assert out["critic_verdict"] == "redirect"
    assert out["revise_count"] == 1
    # Faz 2B: revisions regenerate through the BARE composer, not the
    # tool-bound agent (and the critic no longer injects transcript messages).
    assert "messages" not in out
    merged = {**state, **out}
    assert route_from_critic(merged) == "compose"


async def test_empty_response_increments_revise_count_each_time():
    state = {"messages": [AIMessage(content="")], "revise_count": 1}
    out = await _run(state)
    assert out["revise_count"] == 2


async def test_empty_response_with_budget_exhausted_gets_a_visible_message_not_silence():
    state = {"messages": [AIMessage(content="")], "revise_count": 2}
    out = await _run(state)

    assert out["critic_verdict"] == "accept"
    assert out["response"], "must not silently accept a blank response"
    from langgraph.graph import END
    merged = {**state, **out}
    assert route_from_critic(merged) == END


async def test_non_empty_response_at_exhausted_budget_is_used_verbatim():
    state = {"messages": [AIMessage(content="a real answer")], "revise_count": 2}
    out = await _run(state)
    assert out["response"] == "a real answer"


async def test_normal_simple_exchange_is_unaffected():
    state = {
        "messages": [HumanMessage(content="hi"), AIMessage(content="Hello! How can I help?")],
        "revise_count": 0,
        "user_query": "hi",
    }
    out = await _run(state)
    assert out["critic_verdict"] == "accept"
    assert out["response"] == "Hello! How can I help?"
