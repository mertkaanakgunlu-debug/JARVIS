"""Faz 2 of the 2026-07-19 review hardening — the G17b deterministic layer.

When the fact extractor is degraded (CLOUD_POLICY off/explicit), the facts
block must say so explicitly instead of the ambiguous "(none yet)" the model
was filling by INVENTING personal facts (a city, in the live A/B run), and
the system prompt must carry a standing no-guessing rule for personal facts.
"""
from __future__ import annotations

from jarvis import providers
from jarvis.context_builder import ContextBuilder
from jarvis.prompts.prompt_loader import PromptContext, load_system_prompt


class _StubMemory:
    def __init__(self, facts=None):
        self._facts = facts or []

    def recall(self, query, n=0, session_id=None):
        return ""

    def recall_summaries(self, query, n=0):
        return []

    def recall_facts(self, query, n=0, distance_max=0.0):
        return self._facts

    def recall_procedures(self, query, n=1, distance_max=0.0):
        return []


class _StubTodos:
    def top_open(self, n=5):
        return []


class _StubSessions:
    def top_entities(self, n=5):
        return []


def _build(facts=None):
    builder = ContextBuilder(_StubMemory(facts), _StubTodos(), _StubSessions())
    return builder.build("test query")


def test_facts_block_plain_none_yet_when_not_degraded(monkeypatch):
    monkeypatch.setattr(providers, "_DEGRADED", set())
    assert _build().facts_block == "(none yet)"


def test_facts_block_marks_unavailable_when_extractor_degraded(monkeypatch):
    monkeypatch.setattr(providers, "_DEGRADED", {"fact_extractor"})
    block = _build().facts_block
    assert "UNAVAILABLE" in block
    assert "do NOT guess" in block


def test_facts_block_keeps_facts_but_adds_caveat_when_degraded(monkeypatch):
    monkeypatch.setattr(providers, "_DEGRADED", {"fact_extractor"})
    block = _build([{"fact_text": "favorite city is İzmir"}]).facts_block
    assert "favorite city is İzmir" in block
    assert "UNAVAILABLE" in block


def test_unrelated_degraded_feature_does_not_trigger_marker(monkeypatch):
    monkeypatch.setattr(providers, "_DEGRADED", {"session_summarizer"})
    assert _build().facts_block == "(none yet)"


def test_system_prompt_carries_memory_honesty_rule():
    prompt = load_system_prompt(PromptContext())
    assert "Memory honesty" in prompt
    assert "NEVER guess" in prompt


# ── Agent Runtime rev.2, Faz 5: cross-session blocks off under eval profile ──

class _RaisingCrossSessionMemory(_StubMemory):
    """Proves recall_summaries/recall_facts/recall_procedures are never even
    CALLED under the eval profile -- not just that their results get
    discarded (a real ChromaDB query cost, and the actual contamination
    vector: a call that never happens can never leak an earlier scenario's
    data into this one)."""

    def recall_summaries(self, query, n=0):
        raise AssertionError("recall_summaries must not be called under JARVIS_TEST_MODE")

    def recall_facts(self, query, n=0, distance_max=0.0):
        raise AssertionError("recall_facts must not be called under JARVIS_TEST_MODE")

    def recall_procedures(self, query, n=1, distance_max=0.0):
        raise AssertionError("recall_procedures must not be called under JARVIS_TEST_MODE")


def test_eval_profile_skips_cross_session_recall_calls_entirely(monkeypatch):
    monkeypatch.setenv("JARVIS_TEST_MODE", "1")
    builder = ContextBuilder(_RaisingCrossSessionMemory(), _StubTodos(), _StubSessions())

    ctx = builder.build("test query")  # must not raise

    assert ctx.facts_block == "(none yet)"
    assert ctx.past_sessions_block == "(no relevant past sessions)"
    assert ctx.procedure_block == ""


def test_eval_profile_off_still_calls_cross_session_recall(monkeypatch):
    monkeypatch.delenv("JARVIS_TEST_MODE", raising=False)
    calls: list[str] = []

    class _TrackingMemory(_StubMemory):
        def recall_summaries(self, query, n=0):
            calls.append("summaries")
            return []

        def recall_facts(self, query, n=0, distance_max=0.0):
            calls.append("facts")
            return []

        def recall_procedures(self, query, n=1, distance_max=0.0):
            calls.append("procedures")
            return []

    builder = ContextBuilder(_TrackingMemory(), _StubTodos(), _StubSessions())
    builder.build("test query")

    assert set(calls) == {"summaries", "facts", "procedures"}


def test_eval_profile_does_not_affect_episodic_recall(monkeypatch):
    """recall() (episodic, already session-scoped since Faz 2) must still run
    under the eval profile -- only the three CROSS-session blocks are gated."""
    monkeypatch.setenv("JARVIS_TEST_MODE", "1")
    calls: list[str] = []

    class _TrackingMemory(_RaisingCrossSessionMemory):
        def recall(self, query, n=0, session_id=None):
            calls.append("recall")
            return "some episodic context"

    builder = ContextBuilder(_TrackingMemory(), _StubTodos(), _StubSessions())
    ctx = builder.build("test query", session_id="s1")

    assert calls == ["recall"]
    assert ctx.memory_ctx == "some episodic context"
