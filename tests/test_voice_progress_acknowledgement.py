"""Completion-contract TTFB -- the voice-consumer side of the progress
marker: never spoken as raw JSON, spoken instead as a short, deterministic,
non-success-claiming acknowledgement, and never mistaken for the model's own
answer.

Also pins two pre-existing __jarvis_final__ gaps this same change closed:
jarvis/voice_api.py's run_one_response() had NO handling for it at all (a
correction on an uncontracted turn fell straight through to TTS as literal
JSON); jarvis/cli.py's _run_voice_response() already swallowed it correctly
(Paket A) and must keep doing so with the new progress branch sitting right
above it. See tests/test_voice_confirmation_resume.py for the analogous
resume-side fix in jarvis/voice/session.py's resolve_confirmation().
"""
from __future__ import annotations

import json

import pytest

from jarvis.config import Settings
from jarvis.voice.session import describe_progress


def _progress(kind=None, phase="preparing_required_output"):
    payload = {"__jarvis_progress__": True, "phase": phase}
    if kind:
        payload["kind"] = kind
    return json.dumps(payload)


def _final(text):
    return json.dumps({"__jarvis_final__": True, "text": text})


# ── describe_progress(): the phrase itself never claims success ────────────

@pytest.mark.parametrize("lang,kind,expected", [
    ("tr", "chart", "Grafiği hazırlıyorum."),
    ("tr", None, "İstenen çıktıyı hazırlıyorum."),
    ("tr", "table", "İstenen çıktıyı hazırlıyorum."),
    ("en", "chart", "I'm preparing the requested output."),
    ("en", None, "I'm preparing the requested output."),
])
def test_describe_progress_phrases(lang, kind, expected):
    marker = {"kind": kind} if kind else {}
    assert describe_progress(marker, lang) == expected


def test_describe_progress_never_claims_completion():
    """Forbidden per the task's own examples: 'Grafik hazır.', 'Grafiği
    oluşturdum.', 'İşlem tamamlandı.' -- the tool may not have run yet."""
    phrases = [
        describe_progress({"kind": "chart"}, "tr"),
        describe_progress({}, "tr"),
        describe_progress({}, "en"),
    ]
    forbidden = ("hazır.", "oluşturdum", "tamamlandı", "ready", "done", "created", "finished")
    for phrase in phrases:
        lowered = phrase.lower()
        assert not any(f in lowered for f in forbidden), phrase


@pytest.mark.parametrize("lang,expected", [
    ("tr", "İşlemin sonucunu kesinleştiriyorum."),
    ("en", "I'm finalizing the action's result."),
])
def test_describe_approve_result_progress_without_claiming_completion(lang, expected):
    marker = {"phase": "finalizing_action_result"}
    assert describe_progress(marker, lang) == expected


# ── shared fakes ─────────────────────────────────────────────────────────────

class _FakeEngine:
    def __init__(self):
        self.spoken: list[str] = []

    async def speak_stream(self, text_iter, lang="en"):
        async for chunk in text_iter:
            self.spoken.append(chunk)


class _FakeAgent:
    def __init__(self, tokens):
        self._tokens = list(tokens)
        self.current_model_label = "test-model"

    async def chat_stream(self, text, detected_language=None, transport=None):
        for t in self._tokens:
            yield t


# ── voice_api.run_one_response(): progress spoken, final swallowed ─────────

@pytest.mark.asyncio
async def test_run_one_response_speaks_the_progress_acknowledgement_not_raw_json(monkeypatch):
    from jarvis import voice_api

    monkeypatch.setattr(voice_api.event_bus, "state", lambda *a, **k: None)
    messages: list[tuple] = []
    monkeypatch.setattr(voice_api.event_bus, "message", lambda who, text: messages.append((who, text)))

    agent = _FakeAgent([_progress("chart"), "İşte grafiğiniz."])
    engine = _FakeEngine()

    await voice_api.run_one_response(agent, engine, "grafik çiz", "tr")

    assert engine.spoken == ["Grafiği hazırlıyorum.", "İşte grafiğiniz."]
    assert not any("__jarvis_progress__" in s for s in engine.spoken)
    # run_one_response also logs the user's own utterance (event_bus.message
    # "u", ...) at the start, unrelated to this feature -- the acknowledgement
    # itself must not be reported as if it were JARVIS's answer alongside it.
    assert messages == [("u", "grafik çiz"), ("j", "İşte grafiğiniz.")]


@pytest.mark.asyncio
async def test_run_one_response_swallows_the_final_marker_instead_of_leaking_raw_json(monkeypatch):
    """Regression fix: before this change, this loop had NO __jarvis_final__
    case at all -- a correction on an uncontracted turn fell straight
    through to TTS and to event_bus.message() as literal JSON."""
    from jarvis import voice_api

    monkeypatch.setattr(voice_api.event_bus, "state", lambda *a, **k: None)
    messages: list[tuple] = []
    monkeypatch.setattr(voice_api.event_bus, "message", lambda who, text: messages.append((who, text)))

    agent = _FakeAgent(["Hangi format?", _final("Grafik hazır.")])
    engine = _FakeEngine()

    await voice_api.run_one_response(agent, engine, "grafik çiz", "tr")

    assert engine.spoken == ["Hangi format?"]
    assert not any("__jarvis_final__" in s for s in engine.spoken)
    assert messages == [("u", "grafik çiz"), ("j", "Hangi format?")]


# ── cli.py's _run_voice_response(): the --voice loop's turn driver ─────────

@pytest.mark.asyncio
async def test_cli_voice_response_speaks_progress_but_keeps_it_out_of_the_printed_panel(monkeypatch):
    from jarvis import cli

    agent = _FakeAgent([_progress("chart"), "İşte grafiğiniz."])
    engine = _FakeEngine()
    printed: list[str] = []
    monkeypatch.setattr(cli, "_print_jarvis", lambda text, label: printed.append(text))

    await cli._run_voice_response(agent, engine, "grafik çiz", "tr", Settings(_env_file=None))

    assert engine.spoken == ["Grafiği hazırlıyorum.", "İşte grafiğiniz."]
    assert printed == ["İşte grafiğiniz."], (
        "the spoken acknowledgement must not appear in the printed panel, "
        "nor be glued onto the real answer"
    )


@pytest.mark.asyncio
async def test_cli_voice_response_still_swallows_the_final_marker(monkeypatch):
    """No regression: this call site already handled __jarvis_final__
    correctly (Paket A) before this change -- pinned so the new progress
    branch sitting right above it in the source cannot have broken the
    ordering or the swallow."""
    from jarvis import cli

    agent = _FakeAgent(["Hangi format?", _final("Grafik hazır.")])
    engine = _FakeEngine()
    printed: list[str] = []
    monkeypatch.setattr(cli, "_print_jarvis", lambda text, label: printed.append(text))

    await cli._run_voice_response(agent, engine, "grafik çiz", "tr", Settings(_env_file=None))

    assert engine.spoken == ["Hangi format?"]
    assert printed == ["Hangi format?"]
