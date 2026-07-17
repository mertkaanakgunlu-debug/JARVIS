"""Faz 3 (P1) — qwen3's thinking channel is disabled for routine local turns.

Live verification (2026-07-18, Ollama 0.32): qwen3:8b ships thinking ON, which
dominated local latency (a 1-token "4" answer to "2+2" spent ~172 output tokens
and ~7s reasoning). Ollama honors the OpenAI-standard reasoning_effort on /v1;
"none" disables it (14x faster, and a representative file_write tool call stayed
byte-identical, so tool-calling did not regress). Applied to the fast/local role
only; the reasoning-role local fallback keeps full thinking.

These pin the WIRING (config default + get_llm threading). The accuracy-under-
no-thinking claim is validated separately by the oracle A/B against a live model.
"""
from __future__ import annotations

from jarvis.config import Settings
from jarvis.providers import _make_local
import jarvis.providers as providers


def test_default_is_no_thinking():
    assert Settings().local_reasoning_effort == "none"


def test_make_local_sets_reasoning_effort():
    tier = _make_local(Settings(), 4096, reasoning_effort="none")
    assert tier.model.reasoning_effort == "none"


def test_make_local_without_effort_leaves_thinking_on():
    tier = _make_local(Settings(), 2048)
    assert getattr(tier.model, "reasoning_effort", None) in (None, "", "default")


def _spy_effort(monkeypatch) -> list:
    seen: list = []
    real = providers._make_local

    def spy(settings, max_out, *, reasoning_effort=None):
        seen.append(reasoning_effort)
        return real(settings, max_out, reasoning_effort=reasoning_effort)

    monkeypatch.setattr(providers, "_make_local", spy)
    return seen


def test_fast_role_disables_thinking(monkeypatch):
    seen = _spy_effort(monkeypatch)
    providers.get_llm("fast", Settings())
    assert seen and seen[0] == "none"


def test_reasoning_role_local_fallback_keeps_thinking(monkeypatch):
    seen = _spy_effort(monkeypatch)
    providers.get_llm("reasoning", Settings())
    # the reasoning role's local fallback is built with no effort cap
    assert seen and seen[-1] is None


def test_empty_setting_restores_thinking(monkeypatch):
    seen = _spy_effort(monkeypatch)
    s = Settings()
    s.local_reasoning_effort = ""  # operator opt-out for an A/B thinking-on run
    providers.get_llm("fast", s)
    assert seen and seen[0] is None
