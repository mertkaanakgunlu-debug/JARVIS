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

from jarvis.llm_trace import LlmTraceRecorder, reset_cold_start_tracking


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
    assert summary["response_fallback_used"] is False
    assert summary["turn_had_any_fallback"] is False


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
    assert summary["response_fallback_used"] is True
    assert summary["turn_had_any_fallback"] is True
    assert summary["billable"] is False
    assert summary["provider_error_types"] == ["RuntimeError"]
    assert summary["rate_limit_errors"] == 1


# ── Patch 1.1: response-scoped vs turn-scoped fallback ────────────────────────

def test_critic_fallback_does_not_mark_the_response_as_fallback():
    """The exact review scenario: the agent's primary (tier 0) authored the
    visible answer just fine; only the CRITIC's chain fell back (its tier-0
    Vertex call died, its tier-1 Ollama call judged instead). The label must
    keep naming the agent's primary with no "(fallback)" -- that flag is
    response-scoped -- while turn_had_any_fallback still reports the turn-
    level degradation."""
    rec = LlmTraceRecorder(requested_role="fast")

    rid_agent = uuid4()  # agent primary: fine
    rec.on_chat_model_start({}, None, run_id=rid_agent,
                            metadata=_start_meta("ollama", "qwen2.5:7b-instruct",
                                                 tier=0, node="agent"))
    rec.on_llm_end(_llm_result(), run_id=rid_agent)

    rid_c0 = uuid4()  # critic primary: dies
    rec.on_chat_model_start({}, None, run_id=rid_c0,
                            metadata=_start_meta("vertex", "gemini-2.5-pro",
                                                 billable=True, tier=0, node="critic"))
    rec.on_llm_error(RuntimeError("503 UNAVAILABLE"), run_id=rid_c0)

    rid_c1 = uuid4()  # critic fallback: judges
    rec.on_chat_model_start({}, None, run_id=rid_c1,
                            metadata=_start_meta("ollama", "qwen2.5:7b-instruct",
                                                 tier=1, node="critic"))
    rec.on_llm_end(_llm_result(), run_id=rid_c1)

    summary = rec.turn_summary()
    assert summary["provider"] == "ollama"
    assert summary["model"] == "qwen2.5:7b-instruct"
    assert summary["response_fallback_used"] is False
    assert summary["fallback_used"] is False, "the back-compat alias is response-scoped too"
    assert summary["turn_had_any_fallback"] is True


def test_all_tiers_failed_returns_none_summary():
    rec = LlmTraceRecorder()
    rid = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid, metadata=_start_meta("vertex", "g", tier=0))
    rec.on_llm_error(RuntimeError("down"), run_id=rid)
    assert rec.turn_summary() is None, "no successful call -> no lie, keep the old label"


# ── Patch 1.1: three-state billing plumbing ───────────────────────────────────

class _UsageSpy:
    def __init__(self):
        self.rows: list[dict] = []

    def record(self, **kwargs):
        self.rows.append(kwargs)


def test_jarvis_billing_metadata_reaches_usage_record():
    usage = _UsageSpy()
    rec = LlmTraceRecorder(usage=usage)
    rid = uuid4()
    md = _start_meta("aistudio", "gemini-2.5-flash")
    md["jarvis_billing"] = "unknown"
    rec.on_chat_model_start({}, None, run_id=rid, metadata=md)
    rec.on_llm_end(_llm_result(), run_id=rid)

    assert rec.traces[0].billing == "unknown"
    assert rec.traces[0].billable is False
    assert usage.rows[0]["billing"] == "unknown"


def test_legacy_billable_bool_maps_to_paid_or_free():
    """Metadata stamped before the billing field existed (bool only) must map
    paid/free -- not collapse into unknown/unpriced."""
    rec = LlmTraceRecorder()
    rid1, rid2 = uuid4(), uuid4()
    rec.on_chat_model_start({}, None, run_id=rid1,
                            metadata=_start_meta("vertex", "g", billable=True))
    rec.on_llm_end(_llm_result(), run_id=rid1)
    rec.on_chat_model_start({}, None, run_id=rid2,
                            metadata=_start_meta("ollama", "q", billable=False))
    rec.on_llm_end(_llm_result(), run_id=rid2)

    assert rec.traces[0].billing == "paid" and rec.traces[0].billable is True
    assert rec.traces[1].billing == "free" and rec.traces[1].billable is False


def test_call_with_no_identity_metadata_is_billing_unknown():
    """A real call whose metadata carries no jarvis_* identity at all must be
    tracked as unknown (unpriced), never silently asserted free."""
    rec = LlmTraceRecorder()
    rid = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid, metadata={})
    rec.on_llm_end(_llm_result(), run_id=rid)
    assert rec.traces[0].billing == "unknown"
    assert rec.turn_summary()["billing"] == "unknown"


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


def test_turn_summary_prefers_compose_over_agent_on_tool_turns():
    """Round 3 fix: on a tool turn (agent → tools → accounting → compose) the
    BARE compose node authors the visible answer — the agent call only
    selected tools. The label and the latency/TTFT/cold-start diagnostics
    must come from the compose call, or the thinking-on/off A/B reads the
    wrong invocation on exactly the tool scenarios it exists to measure.
    Distinct model labels per node make the choice observable."""
    rec = LlmTraceRecorder(requested_role="fast")

    rid_agent = uuid4()  # tool-selection call, ran first
    rec.on_chat_model_start({}, None, run_id=rid_agent,
                            metadata=_start_meta("ollama", "agent-tool-selection", node="agent"))
    rec.on_llm_end(_llm_result(200, 30), run_id=rid_agent)

    rid_compose = uuid4()  # authored the visible answer
    rec.on_chat_model_start({}, None, run_id=rid_compose,
                            metadata=_start_meta("ollama", "compose-authored", node="compose"))
    rec.on_llm_end(_llm_result(300, 80), run_id=rid_compose)

    rid_critic = uuid4()  # judges after compose; must never win the label
    rec.on_chat_model_start({}, None, run_id=rid_critic,
                            metadata=_start_meta("vertex", "critic-judge",
                                                 billable=True, node="critic"))
    rec.on_llm_end(_llm_result(), run_id=rid_critic)

    summary = rec.turn_summary()
    assert summary["model"] == "compose-authored"
    assert summary["latency_ms"] == rec.traces[1].latency_ms
    assert summary["calls"] == 3


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
    # Patch 1.1: AI Studio's rate class defaults to "unknown" (a key can be
    # free OR paid), so billable-for-certain is False but the authoritative
    # billing field says unknown, not free.
    assert primary_md["jarvis_billing"] == "unknown"
    assert primary_md["jarvis_billable"] is False
    assert tail_md["jarvis_provider"] == "ollama"
    assert tail_md["jarvis_tier_index"] == 1
    assert tail_md["jarvis_billing"] == "free"
    assert tail_md["jarvis_billable"] is False


# ── Faz 3.2: TTFT + cold-start diagnostics ────────────────────────────────────

def test_first_call_to_a_tier_is_cold_next_is_warm():
    reset_cold_start_tracking()
    rec = LlmTraceRecorder()
    rid1, rid2 = uuid4(), uuid4()
    rec.on_chat_model_start({}, None, run_id=rid1, metadata=_start_meta("ollama", "qwen3:8b"))
    rec.on_llm_end(_llm_result(), run_id=rid1)
    rec.on_chat_model_start({}, None, run_id=rid2, metadata=_start_meta("ollama", "qwen3:8b"))
    rec.on_llm_end(_llm_result(), run_id=rid2)

    assert rec.traces[0].cold_start is True
    assert rec.traces[1].cold_start is False


def test_cold_start_tracking_is_per_tier_not_global():
    reset_cold_start_tracking()
    rec = LlmTraceRecorder()
    rid1, rid2 = uuid4(), uuid4()
    rec.on_chat_model_start({}, None, run_id=rid1, metadata=_start_meta("ollama", "qwen3:8b"))
    rec.on_llm_end(_llm_result(), run_id=rid1)
    # a different model on the same provider is a distinct tier -> still cold
    rec.on_chat_model_start({}, None, run_id=rid2, metadata=_start_meta("ollama", "qwen2.5:7b-instruct"))
    rec.on_llm_end(_llm_result(), run_id=rid2)

    assert rec.traces[0].cold_start is True
    assert rec.traces[1].cold_start is True


def test_cold_start_survives_across_recorder_instances():
    """cold_start is a PROCESS concept: a fresh recorder is built every turn
    (see class docstring), so warmth learned in turn 1 must carry into turn 2's
    recorder — that's the whole point (harness process = one "session")."""
    reset_cold_start_tracking()
    rec1 = LlmTraceRecorder()
    rid1 = uuid4()
    rec1.on_chat_model_start({}, None, run_id=rid1, metadata=_start_meta("ollama", "qwen3:8b"))
    rec1.on_llm_end(_llm_result(), run_id=rid1)

    rec2 = LlmTraceRecorder()  # next turn's recorder
    rid2 = uuid4()
    rec2.on_chat_model_start({}, None, run_id=rid2, metadata=_start_meta("ollama", "qwen3:8b"))
    rec2.on_llm_end(_llm_result(), run_id=rid2)

    assert rec2.traces[0].cold_start is False


def test_ttft_none_when_call_never_streamed():
    """/chat's ainvoke() path never fires on_llm_new_token -- ttft_ms must stay
    an honest None, not a fabricated 0 or a copy of total latency."""
    reset_cold_start_tracking()
    rec = LlmTraceRecorder()
    rid = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid, metadata=_start_meta("ollama", "q"))
    rec.on_llm_end(_llm_result(), run_id=rid)
    assert rec.traces[0].ttft_ms is None


def test_ttft_captured_on_first_token_only(monkeypatch):
    import jarvis.llm_trace as lt
    reset_cold_start_tracking()
    # Only 3 ticks: the ttft-is-not-None guard means a SECOND token never
    # calls monotonic() at all (no redundant syscall per token) -- so "rhaba"
    # below consumes none of these.
    clock = iter([100.0, 100.2, 100.9])  # start, tok1, on_llm_end
    monkeypatch.setattr(lt.time, "monotonic", lambda: next(clock))
    rec = LlmTraceRecorder()
    rid = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid, metadata=_start_meta("ollama", "q"))
    rec.on_llm_new_token("Me", run_id=rid)
    rec.on_llm_new_token("rhaba", run_id=rid)  # second token must NOT move ttft
    rec.on_llm_end(_llm_result(), run_id=rid)

    assert abs(rec.traces[0].ttft_ms - 200.0) < 1e-6  # 100.2 - 100.0
    assert abs(rec.traces[0].latency_ms - 900.0) < 1e-6  # 100.9 - 100.0


def test_turn_summary_surfaces_latency_ttft_cold_start(monkeypatch):
    import jarvis.llm_trace as lt
    reset_cold_start_tracking()
    clock = iter([100.0, 100.15, 100.6])
    monkeypatch.setattr(lt.time, "monotonic", lambda: next(clock))
    rec = LlmTraceRecorder(requested_role="fast")
    rid = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid,
                            metadata=_start_meta("ollama", "qwen3:8b", node="agent"))
    rec.on_llm_new_token("t", run_id=rid)
    rec.on_llm_end(_llm_result(), run_id=rid)

    summary = rec.turn_summary()
    assert summary["cold_start"] is True
    assert abs(summary["ttft_ms"] - 150.0) < 1e-6
    assert abs(summary["latency_ms"] - 600.0) < 1e-6


def test_get_llm_aistudio_paid_mode_marks_tier_billable():
    from jarvis.config import Settings
    from jarvis.providers import get_llm

    settings = Settings(
        _env_file=None,
        gemini_api_key="fake-key",
        cloud_tier="aistudio",
        cloud_policy="auto",
        ai_studio_billing_mode="paid",
    )
    llm = get_llm("reasoning", settings)
    from langchain_core.runnables import RunnableWithFallbacks
    assert isinstance(llm, RunnableWithFallbacks)
    primary_md = llm.runnable.config.get("metadata", {})
    assert primary_md["jarvis_billing"] == "paid"
    assert primary_md["jarvis_billable"] is True
