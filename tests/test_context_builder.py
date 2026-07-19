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
