"""jarvis/cli.py's _print_voice_diagnostics -- Faz D (voice observability)
startup diagnostics: which mic/speaker were picked, sample rate, and whether
Whisper actually loaded onto the requested device. Must be best-effort --
a diagnostics print can never itself crash voice mode startup.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis import cli


class _FakeAudioIO:
    def __init__(self):
        self._settings = SimpleNamespace(audio_input_device="", audio_output_device="")
        self._sample_rate = 16000


class _FakeStt:
    _device = "cuda"


class _FakeModels:
    stt = _FakeStt()


class _FakeEngine:
    def __init__(self):
        self._audio_io = _FakeAudioIO()
        self._models = _FakeModels()


def test_prints_device_sample_rate_and_stt_device(monkeypatch, capsys):
    import sounddevice as sd

    monkeypatch.setattr(sd, "query_devices", lambda selector, kind: {"name": f"Fake {kind}", "index": 0})

    cli._print_voice_diagnostics(_FakeEngine())

    out = capsys.readouterr().out
    assert "Fake input" in out
    assert "Fake output" in out
    assert "16000 Hz" in out
    assert "cuda" in out


def test_never_crashes_when_sounddevice_query_raises(monkeypatch, capsys):
    import sounddevice as sd

    def _boom(selector, kind):
        raise RuntimeError("no such device")

    monkeypatch.setattr(sd, "query_devices", _boom)

    cli._print_voice_diagnostics(_FakeEngine())  # must not raise

    out = capsys.readouterr().out
    assert "unavailable" in out
    assert "cuda" in out  # the STT line is independent of the device query


def test_reports_not_loaded_when_stt_device_is_unset(monkeypatch, capsys):
    import sounddevice as sd

    monkeypatch.setattr(sd, "query_devices", lambda selector, kind: {"name": "x", "index": 0})

    engine = _FakeEngine()
    engine._models.stt._device = None

    cli._print_voice_diagnostics(engine)

    out = capsys.readouterr().out
    assert "not loaded" in out


def test_never_crashes_when_models_is_none():
    """engine.load() may not have run yet in some caller ordering -- must
    degrade gracefully, not AttributeError."""
    engine = _FakeEngine()
    engine._models = None
    cli._print_voice_diagnostics(engine)  # must not raise
