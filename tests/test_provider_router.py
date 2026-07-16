"""jarvis/providers/get_llm() -- the local-first model router (Faz 1).

These are the concrete "offline resilience" claims ROADMAP.md makes for the
local-first pivot: Ollama is primary for the fast/local/realtime roles, and
even the cloud-escalation "reasoning" role has local Ollama as its own final
fallback so nothing is cloud-mandatory. No network access needed here --
constructing a ChatOpenAI/ChatGoogleGenerativeAI client doesn't itself call
out to Ollama or Google; only .invoke() would, and nothing here invokes.

Settings is built with explicit kwargs (never a bare Settings()) so this
suite can never accidentally read the real project's .env -- see
tests/conftest.py's isolated_cwd docstring for why that matters generally;
here it's simpler still, since pydantic-settings' init kwargs take priority
over the dotenv/env-var sources regardless of cwd.
"""
from __future__ import annotations

import pytest
from langchain_core.runnables import RunnableWithFallbacks
from langchain_core.tools import tool

from jarvis.config import Settings
from jarvis.providers import get_llm


def _settings(**overrides) -> Settings:
    defaults = dict(
        gemini_api_key="",
        cloud_tier="flash",
        google_cloud_project="",
        pin_cloud_model=False,
        local_model="qwen2.5:7b-instruct",
        ollama_base_url="http://localhost:11434",
        cloud_model_fallback="gemini-2.5-flash",
        vertex_model_primary="gemini-2.5-pro",
        vertex_model_fast="gemini-2.5-flash",
        # This file tests the ROUTER's own structural claims (which tiers
        # compose in which order) -- CLOUD_POLICY's off-by-default gate is a
        # separate, deliberate concern covered by test_cloud_policy.py. "auto"
        # here reproduces the pre-sprint (pre-CLOUD_POLICY) behavior these
        # tests were written against.
        cloud_policy="auto",
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


@tool
def _dummy_tool(x: str) -> str:
    """A dummy tool used only to exercise bind_tools()."""
    return x


class TestPureLocalNoCloudConfigured:
    """gemini_api_key="" and use_vertex=False -- the "no cloud credentials at
    all" configuration the local-first pivot is explicitly meant to support."""

    def test_fast_role_is_bare_local_no_fallback_wrapper(self):
        settings = _settings()
        llm = get_llm("fast", settings)
        assert not isinstance(llm, RunnableWithFallbacks)
        assert llm.model_name == "qwen2.5:7b-instruct"

    def test_reasoning_role_falls_back_to_local_when_no_cloud(self):
        """The key offline-failover claim: even the escalation tier must not
        be cloud-mandatory. cloud_tiers=[] -> chain=[local] -> bare local,
        not a fallback wrapper around nothing."""
        settings = _settings()
        llm = get_llm("reasoning", settings)
        assert not isinstance(llm, RunnableWithFallbacks)
        assert llm.model_name == "qwen2.5:7b-instruct"

    def test_unknown_role_raises(self):
        settings = _settings()
        with pytest.raises(ValueError):
            get_llm("nonexistent-role", settings)  # type: ignore[arg-type]


class TestCloudConfigured:
    """gemini_api_key set (AI Studio available), still no Vertex project."""

    def test_fast_role_wraps_local_primary_with_cloud_fallback(self):
        settings = _settings(gemini_api_key="fake-test-key")
        llm = get_llm("fast", settings)
        assert isinstance(llm, RunnableWithFallbacks)
        assert llm.runnable.model_name == "qwen2.5:7b-instruct"  # local stays primary
        assert len(llm.fallbacks) == 1

    def test_reasoning_role_puts_local_last_in_the_chain(self):
        """Cloud tiers come first (the actual escalation), but local Ollama
        must always be the final fallback -- never absent."""
        settings = _settings(gemini_api_key="fake-test-key")
        llm = get_llm("reasoning", settings)
        assert isinstance(llm, RunnableWithFallbacks)
        assert len(llm.fallbacks) == 1
        last_fallback = llm.fallbacks[-1]
        assert last_fallback.model_name == "qwen2.5:7b-instruct"

    def test_tools_bind_before_wrapping_in_fallbacks_no_crash(self):
        """Historical bug (Faz 1): binding tools AFTER .with_fallbacks() used
        to raise AttributeError. get_llm() binds first, then wraps -- must not
        raise regardless of whether the underlying library would also allow
        the other order today."""
        settings = _settings(gemini_api_key="fake-test-key")
        llm = get_llm("fast", settings, tools=[_dummy_tool])
        assert llm is not None
        llm2 = get_llm("reasoning", settings, tools=[_dummy_tool])
        assert llm2 is not None


class TestPinnedCloudModel:
    """pin_cloud_model=True (user manually switched model) bypasses the
    Ollama-primary default for fast/local/realtime -- reasoning is untouched."""

    def test_pin_with_no_cloud_available_falls_back_to_local_default(self):
        """If the pin can't even be constructed (no credentials), get_llm()
        must fall back to the local-first default rather than raise."""
        settings = _settings(pin_cloud_model=True, gemini_api_key="")
        llm = get_llm("fast", settings)
        assert llm.model_name == "qwen2.5:7b-instruct"

    def test_reasoning_role_ignores_the_pin(self):
        settings = _settings(pin_cloud_model=True, gemini_api_key="fake-test-key")
        llm = get_llm("reasoning", settings)
        # Still the normal reasoning chain -- pin only affects fast/local/realtime.
        assert isinstance(llm, RunnableWithFallbacks)
        assert llm.fallbacks[-1].model_name == "qwen2.5:7b-instruct"
