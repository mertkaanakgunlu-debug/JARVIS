"""LlmTraceRecorder — runtime truth for provider/model labels (F.1, F.2).

The pre-sprint label was derived from the REQUESTED role, so a turn served by
the Ollama fallback still displayed "Gemini (Vertex, reasoning)". These tests
lock in: (1) the tier metadata stamped by providers.get_llm() reaches the
recorder and names the actual provider; (2) a failed primary tier followed by
a successful fallback tier is reported as fallback_used=True with the
fallback's identity; (3) real LangChain callback plumbing delivers the
with_config metadata end-to-end (no hand-waving about kwargs shapes).
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from jarvis.llm_trace import LlmTraceRecorder


def _start_meta(provider: str, model: str, *, billable=False, tier=0, node=""):
    md = {
        "jarvis_provider": provider,
        "jarvis_model": model,
        "jarvis_billable": billable,
        "jarvis_tier_index": tier,
    }
    if node:
        md["langgraph_node"] = node
    return md


def _llm_result(tokens_in=10, tokens_out=5):
    msg = AIMessage(
        content="hi",
        usage_metadata={"input_tokens": tokens_in, "output_tokens": tokens_out,
                        "total_tokens": tokens_in + tokens_out},
    )
    return SimpleNamespace(generations=[[SimpleNamespace(message=msg)]], llm_output={})


# ── F1: Ollama metadata → actual_provider == "ollama" ─────────────────────────

def test_ollama_call_traced_with_actual_provider():
    rec = LlmTraceRecorder(requested_role="reasoning")
    rid = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid,
                            metadata=_start_meta("ollama", "qwen2.5:7b-instruct"))
    rec.on_llm_end(_llm_result(120, 40), run_id=rid)

    assert len(rec.traces) == 1
    t = rec.traces[0]
    assert t.provider == "ollama"
    assert t.model == "qwen2.5:7b-instruct"
    assert t.billable is False
    assert t.ok is True
    assert (t.input_tokens, t.output_tokens) == (120, 40)

    summary = rec.turn_summary()
    assert summary["provider"] == "ollama"
    assert summary["requested_role"] == "reasoning"
    assert summary["fallback_used"] is False


# ── F2: primary fails, fallback answers → fallback_used=True ─────────────────

def test_failed_primary_then_fallback_success_marks_fallback_used():
    rec = LlmTraceRecorder(requested_role="reasoning")

    rid1 = uuid4()  # vertex primary: dies (e.g. 429)
    rec.on_chat_model_start({}, None, run_id=rid1,
                            metadata=_start_meta("vertex", "gemini-2.5-pro",
                                                 billable=True, tier=0))
    rec.on_llm_error(RuntimeError("429 RESOURCE_EXHAUSTED"), run_id=rid1)

    rid2 = uuid4()  # ollama fallback: answers
    rec.on_chat_model_start({}, None, run_id=rid2,
                            metadata=_start_meta("ollama", "qwen2.5:7b-instruct", tier=1))
    rec.on_llm_end(_llm_result(), run_id=rid2)

    assert [t.ok for t in rec.traces] == [False, True]
    summary = rec.turn_summary()
    assert summary["provider"] == "ollama"
    assert summary["model"] == "qwen2.5:7b-instruct"
    assert summary["fallback_used"] is True
    assert summary["billable"] is False


def test_all_tiers_failed_returns_none_summary():
    rec = LlmTraceRecorder()
    rid = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid, metadata=_start_meta("vertex", "g", tier=0))
    rec.on_llm_error(RuntimeError("down"), run_id=rid)
    assert rec.turn_summary() is None, "no successful call -> no lie, keep the old label"


# ── node preference: the agent node's call names the turn ─────────────────────

def test_turn_summary_prefers_agent_node_over_critic():
    rec = LlmTraceRecorder(requested_role="fast")

    rid_agent = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid_agent,
                            metadata=_start_meta("ollama", "qwen2.5:7b-instruct", node="agent"))
    rec.on_llm_end(_llm_result(), run_id=rid_agent)

    rid_critic = uuid4()  # critic runs AFTER the agent, on the reasoning chain
    rec.on_chat_model_start({}, None, run_id=rid_critic,
                            metadata=_start_meta("vertex", "gemini-2.5-pro",
                                                 billable=True, node="critic"))
    rec.on_llm_end(_llm_result(), run_id=rid_critic)

    summary = rec.turn_summary()
    assert summary["provider"] == "ollama", "the agent node authored the reply, not the critic"
    assert summary["calls"] == 2


# ── de-dup + latency plumbing ─────────────────────────────────────────────────

def test_on_llm_start_fallback_does_not_double_register():
    rec = LlmTraceRecorder()
    rid = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid, metadata=_start_meta("ollama", "q"))
    rec.on_llm_start({}, ["prompt"], run_id=rid, metadata=_start_meta("ollama", "SHOULD-NOT-WIN"))
    rec.on_llm_end(_llm_result(), run_id=rid)
    assert len(rec.traces) == 1
    assert rec.traces[0].model == "q"


def test_latency_measured_per_run(monkeypatch):
    import jarvis.llm_trace as lt
    clock = iter([100.0, 100.5])
    monkeypatch.setattr(lt.time, "monotonic", lambda: next(clock))
    rec = LlmTraceRecorder()
    rid = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid, metadata=_start_meta("ollama", "q"))
    rec.on_llm_end(_llm_result(), run_id=rid)
    assert abs(rec.traces[0].latency_ms - 500.0) < 1e-6


# ── end-to-end through REAL LangChain callback plumbing ───────────────────────

def test_real_langchain_plumbing_delivers_tier_metadata():
    """with_config(metadata=...) must arrive at the recorder via an actual
    .invoke() — the same mechanism providers.get_llm() relies on. This is the
    offline probe from the plan, promoted to a regression test."""
    rec = LlmTraceRecorder(requested_role="fast")
    model = FakeMessagesListChatModel(responses=[
        AIMessage(content="pong",
                  usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}),
    ])
    tagged = model.with_config(metadata=_start_meta("ollama", "qwen2.5:7b-instruct"))

    tagged.invoke("ping", config={"callbacks": [rec]})

    assert len(rec.traces) == 1
    t = rec.traces[0]
    assert t.provider == "ollama"
    assert t.model == "qwen2.5:7b-instruct"
    assert t.ok and (t.input_tokens, t.output_tokens) == (7, 3)


def test_get_llm_tiers_carry_metadata():
    """The composed router output itself: every tier of a reasoning chain is a
    RunnableBinding whose config carries the jarvis_* identity keys, in chain
    order (tier_index 0..n), with the local tail non-billable."""
    from jarvis.config import Settings
    from jarvis.providers import get_llm

    settings = Settings(
        _env_file=None,
        gemini_api_key="fake-key",
        cloud_tier="aistudio",
        cloud_policy="auto",  # this test is about tier metadata shape, not gating
    )
    llm = get_llm("reasoning", settings)

    # aistudio tier + ollama tail => RunnableWithFallbacks(primary, [tail])
    from langchain_core.runnables import RunnableWithFallbacks
    assert isinstance(llm, RunnableWithFallbacks)
    primary_md = llm.runnable.config.get("metadata", {})
    tail_md = llm.fallbacks[-1].config.get("metadata", {})
    assert primary_md["jarvis_provider"] == "aistudio"
    assert primary_md["jarvis_tier_index"] == 0
    assert primary_md["jarvis_billable"] is False
    assert tail_md["jarvis_provider"] == "ollama"
    assert tail_md["jarvis_tier_index"] == 1
    assert tail_md["jarvis_billable"] is False
