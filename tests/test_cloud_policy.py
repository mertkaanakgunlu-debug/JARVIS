"""CLOUD_POLICY gating (stabilization sprint, F.9).

Locks in the structural guarantee behind CLOUD_POLICY=off (the new default,
per the owner's live-tested decision after AI Studio's key turned out to be
exhausted and Vertex was billing on every non-trivial turn): no role, no
fallback tier, and none of the 8 direct-Gemini helper modules may construct
or invoke a Google model. "off" must be a hard structural block, not routing
advice a busy/confused model could route around.
"""
from __future__ import annotations


import pytest
from langchain_core.runnables import RunnableWithFallbacks

from jarvis.config import Settings
from jarvis.providers import (
    cloud_extractors_enabled,
    degraded_features,
    get_llm,
    note_degraded,
)


def _settings(**overrides) -> Settings:
    defaults = dict(
        gemini_api_key="fake-key",       # deliberately configured...
        cloud_tier="vertex",
        google_cloud_project="fake-project",  # ...so a leak would be visible
        pin_cloud_model=False,
        local_model="qwen2.5:7b-instruct",
        ollama_base_url="http://localhost:11434",
        cloud_model_fallback="gemini-2.5-flash",
        vertex_model_primary="gemini-2.5-pro",
        vertex_model_fast="gemini-2.5-flash",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


class TestPolicyOffBlocksEveryRole:
    def test_fast_role_is_pure_local_no_fallback_chain(self):
        settings = _settings(cloud_policy="off")
        llm = get_llm("fast", settings)
        assert not isinstance(llm, RunnableWithFallbacks)
        assert llm.model_name == "qwen2.5:7b-instruct"

    def test_reasoning_role_is_pure_local_despite_vertex_configured(self):
        """The critical claim: reasoning normally escalates to cloud first --
        under off, it must not, even with real-looking Vertex credentials."""
        settings = _settings(cloud_policy="off")
        llm = get_llm("reasoning", settings)
        assert not isinstance(llm, RunnableWithFallbacks)
        assert llm.model_name == "qwen2.5:7b-instruct"

    def test_pin_cloud_model_does_not_bypass_off(self):
        """A manual switch_model() pin is still an automatic-router concern --
        off means off, no exceptions (see the module docstring)."""
        settings = _settings(cloud_policy="off", pin_cloud_model=True)
        llm = get_llm("fast", settings)
        assert not isinstance(llm, RunnableWithFallbacks)
        assert llm.model_name == "qwen2.5:7b-instruct"


class TestPolicyExplicitAllowsOnlyThePin:
    def test_automatic_fallback_still_suppressed(self):
        settings = _settings(cloud_policy="explicit", pin_cloud_model=False)
        llm = get_llm("fast", settings)
        assert not isinstance(llm, RunnableWithFallbacks), \
            "unpinned fast role must not silently reach for cloud under explicit"

    def test_automatic_reasoning_escalation_still_suppressed(self):
        settings = _settings(cloud_policy="explicit")
        llm = get_llm("reasoning", settings)
        assert not isinstance(llm, RunnableWithFallbacks)

    def test_manual_pin_is_honored(self):
        """The whole point of "explicit": a user who typed /model vertex/...
        still gets it, even though nothing else escalates automatically."""
        settings = _settings(cloud_policy="explicit", pin_cloud_model=True)
        llm = get_llm("fast", settings)
        assert isinstance(llm, RunnableWithFallbacks)
        assert llm.runnable.model == "gemini-2.5-flash"  # vertex_model_fast tier


class TestPolicyAutoUnchanged:
    def test_reasoning_role_escalates_to_cloud_first(self):
        settings = _settings(cloud_policy="auto")
        llm = get_llm("reasoning", settings)
        assert isinstance(llm, RunnableWithFallbacks)
        assert llm.runnable.model == "gemini-2.5-pro"  # vertex_model_primary tier


# ── cloud_extractors_enabled / note_degraded / degraded_features ─────────────

def test_cloud_extractors_enabled_false_under_off():
    assert cloud_extractors_enabled(_settings(cloud_policy="off")) is False


def test_cloud_extractors_enabled_true_under_explicit_and_auto():
    assert cloud_extractors_enabled(_settings(cloud_policy="explicit")) is True
    assert cloud_extractors_enabled(_settings(cloud_policy="auto")) is True


def test_note_degraded_warns_once_per_feature(monkeypatch):
    import jarvis.providers as providers
    monkeypatch.setattr(providers, "_DEGRADED", set())
    note_degraded("test_feature_x")
    note_degraded("test_feature_x")
    note_degraded("test_feature_y")
    assert degraded_features() == ["test_feature_x", "test_feature_y"]


# ── the 8 direct-Gemini call sites: gate fires before any construction ───────
# Each extractor wraps its whole body in `except Exception: return <empty>`,
# so a monkeypatched "raise if constructed" stand-in would be silently
# swallowed by that same except and prove nothing either way. The real
# regression signal is note_degraded(feature) firing -- that call exists on
# NO other code path, so its presence in degraded_features() only happens
# when the gate itself actually ran.

@pytest.mark.asyncio
async def test_fact_extractor_gated(monkeypatch):
    import jarvis.fact_extractor as mod
    monkeypatch.setattr(providers_module(), "_DEGRADED", set())
    result = await mod.extract_facts("hi", "hello", _settings(cloud_policy="off"))
    assert result == []
    assert "fact_extractor" in degraded_features()


@pytest.mark.asyncio
async def test_entity_extractor_gated(monkeypatch):
    import jarvis.entity_extractor as mod
    result = await mod.extract_entities("hi", "hello", _settings(cloud_policy="off"))
    assert result == []
    assert "entity_extractor" in degraded_features()


@pytest.mark.asyncio
async def test_finance_extractor_gated():
    from jarvis.finance_extractor import extract_transaction
    result = await extract_transaction("subj", "body", _settings(cloud_policy="off"))
    assert result is None
    assert "finance_extractor" in degraded_features()


@pytest.mark.asyncio
async def test_session_summarizer_gated():
    from jarvis.session_summarizer import summarize_session
    from langchain_core.messages import HumanMessage, AIMessage
    msgs = [HumanMessage(content="a"), AIMessage(content="b"),
            HumanMessage(content="c"), AIMessage(content="d")]
    result = await summarize_session(msgs, _settings(cloud_policy="off"))
    assert result == ""
    assert "session_summarizer" in degraded_features()


@pytest.mark.asyncio
async def test_todo_analyzer_single_gated():
    from jarvis.todo_analyzer import analyze_todo
    result = await analyze_todo("title", "desc", _settings(cloud_policy="off"))
    assert result is None
    assert "todo_analyzer" in degraded_features()


@pytest.mark.asyncio
async def test_todo_analyzer_batch_gated():
    from jarvis.todo_analyzer import analyze_all_todos
    result = await analyze_all_todos([{"id": "1", "title": "x"}], _settings(cloud_policy="off"))
    assert result is None
    assert "todo_analyzer" in degraded_features()


def test_email_triage_gated(monkeypatch):
    from jarvis.tools import email_triage
    monkeypatch.setattr(email_triage, "_get_gmail_service", lambda settings: object())
    result = email_triage.triage_emails("q", 5, _settings(cloud_policy="off"))
    assert "disabled" in result.lower() or "CLOUD_POLICY" in result
    assert "email_triage" in degraded_features()


def test_pdf_vision_gated(tmp_path):
    from jarvis.tools.pdf_vision import read_pdf_vision
    fake_pdf = tmp_path / "x.pdf"
    fake_pdf.write_bytes(b"%PDF-1.4 fake")
    result = read_pdf_vision(fake_pdf, "what is this?", _settings(cloud_policy="off"))
    assert "[ERROR]" in result
    assert "pdf_vision" in degraded_features()


def test_deep_research_gated(monkeypatch):
    from jarvis.tools import deep_research
    monkeypatch.setattr(
        deep_research, "tavily_search_raw",
        lambda topic, key, max_results=7: [{"url": "http://x", "title": "T", "content": "c"}],
    )
    monkeypatch.setattr(deep_research, "fetch_url", lambda url, settings, max_chars=3000: "some content")
    result = deep_research.run_deep_research("topic", _settings(cloud_policy="off"))
    assert "CLOUD_POLICY" in result
    assert "deep_research" in degraded_features()


def providers_module():
    import jarvis.providers as providers
    return providers
