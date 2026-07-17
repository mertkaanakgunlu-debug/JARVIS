"""Sprint 2 Faz 2B — bare composer, budgeted post-tool routing, ephemeral critic.

The structural target is live incident F16 (2026-07-16): after a SUCCESSFUL
procedure_save the tool-bound agent re-issued the same call ~10 times until
GRAPH_RECURSION_LIMIT killed the turn. The fixes under test:
  * after a tool round, the default next hop is a composer with ZERO tools
    bound (it cannot re-issue anything),
  * re-entering the tool-bound agent is allowed only for multi-step-shaped
    turns within the round budget,
  * critic feedback rides in state and is applied node-locally — the fake
    "[Quality Critic — …]" HumanMessage no longer pollutes the transcript.
"""
from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from jarvis.config import Settings
from jarvis.graph.nodes import (
    make_compose_node,
    make_critic_node,
    make_route_after_tool_accounting,
    route_from_critic,
)
from jarvis.graph.tool_router import classify_query


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def _executed_round(tool: str, *, ok: bool, retryable: bool = False) -> list:
    """A finished tool round: AIMessage(tool_calls) + its ToolMessage result."""
    ai = AIMessage(content="", tool_calls=[
        {"name": tool, "args": {"x": 1}, "id": "c1", "type": "tool_call"},
    ])
    if ok:
        tm = ToolMessage(content="done", tool_call_id="c1")
    else:
        tm = ToolMessage(
            content=(
                f"[TOOL_ERROR]\ntool={tool}\ncategory=timeout\nmessage=slow\n"
                f"retryable={'true' if retryable else 'false'}"
            ),
            tool_call_id="c1",
        )
    return [ai, tm]


# ── route_after_tool_accounting ───────────────────────────────────────────────

def test_single_domain_success_goes_to_compose():
    router = make_route_after_tool_accounting(_settings())
    state = {
        "messages": [HumanMessage(content="q")] + _executed_round("procedure_save", ok=True),
        "tool_rounds": 1,
        "tool_route": classify_query("Şu prosedürü kaydet: adımlar").to_dict(),
    }
    assert router(state) == "compose"  # F16'nin döngüsü burada kapanır


def test_multi_domain_success_reenters_agent_within_budget():
    router = make_route_after_tool_accounting(_settings())
    state = {
        "messages": [HumanMessage(content="q")] + _executed_round("pdf_read", ok=True),
        "tool_rounds": 1,
        "tool_route": classify_query("Bu PDF içindeki toplantıları takvimime ekle").to_dict(),
    }
    assert router(state) == "agent"


def test_round_budget_exhaustion_forces_compose():
    router = make_route_after_tool_accounting(_settings(max_tool_rounds_per_turn=2))
    state = {
        "messages": [HumanMessage(content="q")] + _executed_round("pdf_read", ok=True),
        "tool_rounds": 2,
        "tool_route": classify_query("Bu PDF içindeki toplantıları takvimime ekle").to_dict(),
        "needs_planning": True,
    }
    assert router(state) == "compose"


def test_planner_path_counts_as_multi_step():
    router = make_route_after_tool_accounting(_settings())
    state = {
        "messages": [HumanMessage(content="q")] + _executed_round("file_read", ok=True),
        "tool_rounds": 1,
        "tool_route": classify_query("dosyayı oku").to_dict(),
        "needs_planning": True,
    }
    assert router(state) == "agent"


def test_retryable_failure_gets_one_more_round():
    router = make_route_after_tool_accounting(_settings())
    state = {
        "messages": [HumanMessage(content="q")] + _executed_round("url_read", ok=False, retryable=True),
        "tool_rounds": 1,
    }
    assert router(state) == "agent"


def test_non_retryable_failure_composes():
    router = make_route_after_tool_accounting(_settings())
    state = {
        "messages": [HumanMessage(content="q")] + _executed_round("itu_mail", ok=False, retryable=False),
        "tool_rounds": 1,
    }
    assert router(state) == "compose"


# ── compose node: bare model + ephemeral critique ─────────────────────────────

class _RecordingBareLLM:
    def __init__(self, log):
        self.log = log

    async def ainvoke(self, messages):
        self.log.append(list(messages))
        return AIMessage(content="final answer")


@pytest.mark.asyncio
async def test_compose_uses_bare_model_and_keeps_critique_ephemeral(monkeypatch):
    import jarvis.providers as providers

    calls: list = []
    bound_with: list = []

    def fake_get_llm(role, settings, *, tools=None, max_output_tokens=None):
        bound_with.append(tools)
        return _RecordingBareLLM(calls)

    monkeypatch.setattr(providers, "get_llm", fake_get_llm)
    node = make_compose_node(_settings())

    state = {
        "messages": [HumanMessage(content="soru")],
        "use_pro_agent": False,
        "critic_verdict": "revise",
        "critique": "Add a concrete example.",
    }
    out = await node(state)

    # bare: composed with NO tools bound
    assert bound_with == [None]
    # critique reached the model invocation as a SystemMessage...
    sent = calls[0]
    assert any(isinstance(m, SystemMessage) and "Add a concrete example." in m.content for m in sent)
    # ...but is NOT persisted into graph state — only the answer comes back
    assert len(out["messages"]) == 1
    assert isinstance(out["messages"][0], AIMessage)
    assert out["response"] == "final answer"


@pytest.mark.asyncio
async def test_compose_without_critique_sends_transcript_unchanged(monkeypatch):
    import jarvis.providers as providers

    calls: list = []
    monkeypatch.setattr(
        providers, "get_llm",
        lambda role, settings, *, tools=None, max_output_tokens=None: _RecordingBareLLM(calls),
    )
    node = make_compose_node(_settings())
    msgs = [HumanMessage(content="soru")] + _executed_round("file_read", ok=True)
    await node({"messages": msgs, "critique": "", "critic_verdict": ""})
    assert calls[0] == msgs  # no synthetic additions


# ── critic: no transcript injection anymore ───────────────────────────────────

class _VerdictLLM:
    def __init__(self, verdict: str, critique: str):
        self._payload = f'{{"score": 4, "verdict": "{verdict}", "critique": "{critique}"}}'

    async def ainvoke(self, messages):
        return AIMessage(content=self._payload)


@pytest.mark.asyncio
async def test_critic_revise_carries_feedback_in_state_only():
    node = make_critic_node(_VerdictLLM("revise", "too short"))
    # "analiz" keyword'ü _is_simple_exchange fast-path'ini atlatır — kritik
    # gerçekten çağrılsın ki revise yolunu test edebilelim.
    query = "bu veriyi analiz edip bana detaylı bir değerlendirme yapar mısın"
    state = {
        "messages": [
            HumanMessage(content=query),
            AIMessage(content="kısa cevap ama yeterince uzun olmayan bir taslak metin"),
        ],
        "user_query": query,
        "revise_count": 0,
    }
    out = await node(state)
    assert out["critic_verdict"] == "revise"
    assert out["critique"] == "too short"
    assert "messages" not in out  # hiçbir sahte Human/System mesajı enjekte edilmez


@pytest.mark.asyncio
async def test_critic_empty_response_redirects_without_injection():
    node = make_critic_node(_VerdictLLM("accept", ""))
    state = {
        "messages": [HumanMessage(content="soru"), AIMessage(content="")],
        "user_query": "yeterince uzun ve karmaşık bir kullanıcı sorusu örneği burada",
        "revise_count": 0,
    }
    out = await node(state)
    assert out["critic_verdict"] == "redirect"
    assert out["revise_count"] == 1
    assert "messages" not in out


def test_route_from_critic_revise_goes_to_compose():
    assert route_from_critic({"critic_verdict": "revise", "revise_count": 1}) == "compose"
    assert route_from_critic({"critic_verdict": "redirect", "revise_count": 1}) == "compose"
    # budget exhausted / accepted → END
    from langgraph.graph import END
    assert route_from_critic({"critic_verdict": "revise", "revise_count": 2}) == END
    assert route_from_critic({"critic_verdict": "accept", "revise_count": 0}) == END
