"""NVIDIA NIM construction, profiles, fallback, and credential safety."""

from __future__ import annotations

import logging

from langchain_core.runnables import RunnableWithFallbacks
from langchain_core.tools import tool

from jarvis.config import Settings
from jarvis.providers import _safe_construct, get_llm
from jarvis.providers.nvidia import NVIDIA_TOURNAMENT_MODELS, request_profile


def _settings(**overrides) -> Settings:
    values = {
        "cloud_policy": "roles",
        "gemini_api_key": "",
        "google_cloud_project": "",
        "local_model": "qwen3:8b",
        "nvidia_api_key": "nvapi-test-secret-value",
        "nvidia_fast_model": "nvidia/nemotron-3-super-120b-a12b",
        "nvidia_reasoning_model": "mistralai/mistral-medium-3.5-128b",
        "nvidia_cloud_first": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@tool
def lookup(city: str) -> str:
    """Synthetic read tool."""
    return city


def _metadata(runnable) -> dict:
    return runnable.config["metadata"]


def test_missing_nvidia_key_never_breaks_local_startup():
    settings = _settings(nvidia_api_key="")
    fast = get_llm("fast", settings)
    reasoning = get_llm("reasoning", settings)
    assert not isinstance(fast, RunnableWithFallbacks)
    assert not isinstance(reasoning, RunnableWithFallbacks)
    assert fast.model_name == reasoning.model_name == "qwen3:8b"


def test_cloud_first_fast_has_nvidia_primary_and_local_fallback_metadata():
    llm = get_llm("fast", _settings())
    assert isinstance(llm, RunnableWithFallbacks)
    assert _metadata(llm.runnable) == {
        "jarvis_provider": "nvidia",
        "jarvis_model": "nvidia/nemotron-3-super-120b-a12b",
        "jarvis_billing": "unknown",
        "jarvis_billable": False,
        "jarvis_tier_index": 0,
        "jarvis_role": "fast",
        "jarvis_primary_provider": "nvidia",
        "jarvis_primary_model": "nvidia/nemotron-3-super-120b-a12b",
    }
    assert _metadata(llm.fallbacks[0])["jarvis_provider"] == "ollama"
    assert _metadata(llm.fallbacks[0])["jarvis_tier_index"] == 1
    raw = llm.runnable.bound
    assert raw.openai_api_base == "https://integrate.api.nvidia.com/v1"
    assert raw.request_timeout == 90.0
    assert raw.max_retries == 1
    assert raw.streaming is False, "astream support must not force all invocations to stream"
    assert raw.extra_body == {
        "chat_template_kwargs": {
            "enable_thinking": False,
            "force_nonempty_content": True,
        }
    }


def test_reasoning_uses_its_independent_promoted_model():
    llm = get_llm("reasoning", _settings())
    assert isinstance(llm, RunnableWithFallbacks)
    assert _metadata(llm.runnable)["jarvis_model"] == "mistralai/mistral-medium-3.5-128b"
    assert _metadata(llm.runnable)["jarvis_role"] == "reasoning"


def test_tools_bind_before_nvidia_fallback_composition():
    fast = get_llm("fast", _settings(), tools=[lookup])
    reasoning = get_llm("reasoning", _settings(), tools=[lookup])
    assert isinstance(fast, RunnableWithFallbacks)
    assert isinstance(reasoning, RunnableWithFallbacks)
    assert _metadata(fast.runnable)["jarvis_provider"] == "nvidia"
    assert _metadata(reasoning.runnable)["jarvis_provider"] == "nvidia"


def test_local_role_is_a_hard_offline_boundary_even_when_cloud_is_promoted():
    llm = get_llm("local", _settings(pin_cloud_model=True))
    assert not isinstance(llm, RunnableWithFallbacks)
    assert _metadata(llm)["jarvis_provider"] == "ollama"


def test_rollback_switch_restores_local_first_order():
    llm = get_llm("fast", _settings(nvidia_cloud_first=False))
    assert isinstance(llm, RunnableWithFallbacks)
    assert _metadata(llm.runnable)["jarvis_provider"] == "ollama"
    assert _metadata(llm.fallbacks[0])["jarvis_provider"] == "nvidia"


def test_all_tournament_models_have_reviewed_role_profiles():
    for model in NVIDIA_TOURNAMENT_MODELS:
        assert request_profile(model, "fast")
        assert request_profile(model, "reasoning")


def test_profiles_do_not_copy_ollama_reasoning_parameter_to_nemotron_or_glm():
    nemotron = request_profile("nvidia/nemotron-3-super-120b-a12b", "reasoning")
    glm = request_profile("z-ai/glm-5.2", "fast")
    mistral = request_profile("mistralai/mistral-medium-3.5-128b", "reasoning")
    assert nemotron.reasoning_effort is None
    assert nemotron.extra_body["chat_template_kwargs"]["enable_thinking"] is True
    assert nemotron.use_reasoning_budget is True
    assert glm.reasoning_effort is None and glm.seed == 42
    assert mistral.reasoning_effort == "high" and mistral.temperature == 0.7


def test_nvidia_key_is_redacted_from_settings_and_constructor_logs(caplog):
    secret = "nvapi-super-secret-value"
    settings = _settings(nvidia_api_key=secret)
    assert secret not in repr(settings)
    with caplog.at_level(logging.INFO, logger="jarvis.providers"):
        assert _safe_construct("nvidia:test", lambda: (_ for _ in ()).throw(RuntimeError(secret))) is None
    assert secret not in caplog.text
