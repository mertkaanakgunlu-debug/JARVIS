"""jarvis/voice/stt_whisper.py -- decoder language lock + beam config.

Live incident (2026-07-24): a Turkish command was transcribed as Russian
(`разденьемся`) because transcribe() never passed `language=`, so Whisper
auto-detected per utterance and mis-fired. The fix forces the decode language
(default "tr") and only runs the Turkish-character metadata backstop in "auto"
mode. These tests inject a fake ct2 model (no real Whisper load) and assert the
kwargs actually reach `model.transcribe(...)` and that the backstop is scoped.
"""
from __future__ import annotations

import numpy as np

from jarvis.config import Settings
from jarvis.voice.stt_whisper import WhisperSTT


class _FakeInfo:
    def __init__(self, language):
        self.language = language


class _FakeSeg:
    def __init__(self, text):
        self.text = text


class _FakeModel:
    """Records the kwargs transcribe() passes; echoes the forced language back
    as info.language (Whisper's own behavior when language= is given)."""
    def __init__(self, text=" merhaba"):
        self.text = text
        self.calls: list[dict] = []

    def transcribe(self, audio, beam_size=5, language=None):
        self.calls.append({"beam_size": beam_size, "language": language})
        # When no language is forced, pretend Whisper mis-detected Russian.
        return ([_FakeSeg(self.text)], _FakeInfo(language or "ru"))


def _stt_with(model, **settings_kw):
    stt = WhisperSTT(Settings(_env_file=None, **settings_kw))
    stt._model = model      # bypass the real _ensure_model()/CUDA load
    stt._device = "cpu"
    return stt


_AUDIO = np.zeros(16000, dtype=np.float32)


def test_default_forces_turkish_into_transcribe():
    m = _FakeModel()
    stt = _stt_with(m)  # default whisper_language == "tr"
    text, lang = stt.transcribe(_AUDIO)
    assert m.calls[0]["language"] == "tr"
    assert lang == "tr"


def test_beam_size_setting_is_honored():
    m = _FakeModel()
    stt = _stt_with(m, whisper_beam_size=1)
    stt.transcribe(_AUDIO)
    assert m.calls[0]["beam_size"] == 1


def test_auto_passes_none_and_keeps_the_tr_char_backstop():
    # auto → language=None → fake reports "ru", but the Turkish characters in the
    # decoded text trip the backstop and correct the tag to "tr".
    m = _FakeModel(text="işçi çalışması")
    stt = _stt_with(m, whisper_language="auto")
    _, lang = stt.transcribe(_AUDIO)
    assert m.calls[0]["language"] is None
    assert lang == "tr"


def test_forced_en_does_not_get_re_tagged_tr_by_the_backstop():
    # Even if Turkish characters appear, a forced "en" session must stay "en" --
    # the backstop only runs in auto mode.
    m = _FakeModel(text="the çı character appears")
    stt = _stt_with(m, whisper_language="en")
    _, lang = stt.transcribe(_AUDIO)
    assert m.calls[0]["language"] == "en"
    assert lang == "en"
