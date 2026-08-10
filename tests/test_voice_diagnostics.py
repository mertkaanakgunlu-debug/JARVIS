"""jarvis/voice/diagnostics.py + its two surfaces (the CLI's /voice-status
and GET /voice/status).

The review finding this closes: Faz D made the telemetry *measurable*
(queue depth, input status/overflow counters, output underruns, per-turn
VAD probabilities, the device Whisper really loaded onto) but not
*observable* -- nothing assembled them, and the only reader was a
four-static-field CLI print that ran once at startup before anything had
happened. These tests pin that one snapshot builder and that both surfaces
read it rather than re-deriving their own.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.voice.diagnostics import (
    VoiceDiagnosticsSnapshot, clear_session, collect, current_snapshot,
    register_session, restore_session,
)
from jarvis.voice.state import VoiceState


class _FakeAudioIO:
    def __init__(self, **overrides):
        self._settings = SimpleNamespace(audio_input_device="", audio_output_device="")
        self._sample_rate = 16000
        self.input_status_count = 3
        self.input_overflow_count = 1
        self.underrun_count = 2
        self.capture_callback_count = 11
        self.capture_sample_count = 5632
        self.capture_consumed_frame_count = 10
        self.capture_consumed_sample_count = 5120
        self.capture_frame_size_mismatch_count = 0
        self.capture_queue_initial_depth = 0
        self.capture_queue_high_watermark = 2
        self.capture_input_status_count = 1
        self.capture_input_overflow_count = 0
        self._rms = 0.42
        for k, v in overrides.items():
            setattr(self, k, v)

    def mic_level(self) -> float:
        return self._rms

    def queue_depth(self) -> int:
        return 7


class _FakeEngine:
    def __init__(self, audio_io=None, stt_device="cuda", last_turn=None):
        self._audio_io = audio_io if audio_io is not None else _FakeAudioIO()
        self._models = SimpleNamespace(stt=SimpleNamespace(_device=stt_device))
        self._settings = SimpleNamespace(vad_speech_threshold=0.5)
        self.capture_metrics = {
            "vad_frame_count": 10,
            "frame_contract_mismatch_count": 0,
            "speech_started_count": 1,
            "turn_ended_count": 1,
            "stt_attempt_count": 1,
            "stt_nonempty_count": 1,
            "recent_frame_count": 10,
            "recent_input_rms_max": 0.42,
            "recent_vad_prob_max": 0.98,
            "recent_vad_prob_mean": 0.61,
        }
        self.last_turn_metrics = last_turn if last_turn is not None else {}


@pytest.fixture(autouse=True)
def _clean_registry():
    clear_session()
    yield
    clear_session()


# ── collect() ───────────────────────────────────────────────────────────────

def test_collects_devices_counters_and_reducer_state():
    state = VoiceState()
    state.set_capture("listening")
    state.set_response("thinking")

    snap = collect(_FakeEngine(), state, query_devices=False)

    assert snap.sample_rate == 16000
    assert snap.stt_device == "cuda"
    assert snap.mic_rms == pytest.approx(0.42)
    assert snap.queue_depth == 7
    assert snap.input_status_count == 3
    assert snap.input_overflow_count == 1
    assert snap.output_underrun_count == 2
    assert snap.capture_callback_count == 11
    assert snap.capture_consumed_frame_count == 10
    assert snap.capture_queue_high_watermark == 2
    assert snap.capture_frame_size_mismatch_count == 0
    assert snap.capture_input_overflow_count == 0
    assert snap.vad_frame_count == 10
    assert snap.speech_started_count == 1
    assert snap.turn_ended_count == 1
    assert snap.stt_attempt_count == 1
    assert snap.stt_nonempty_count == 1
    assert snap.recent_vad_prob_max == pytest.approx(0.98)
    assert snap.vad_speech_threshold == pytest.approx(0.5)
    assert (snap.capture, snap.response) == ("listening", "thinking")
    assert snap.display == "thinking"
    assert snap.hud_state == "thinking"


def test_reports_the_device_whisper_actually_loaded_onto():
    """The owner's own incident: CUDA silently falling back to CPU with no
    visible sign short of reading logs."""
    snap = collect(_FakeEngine(stt_device="cpu"), query_devices=False)
    assert snap.stt_device == "cpu"


def test_unloaded_stt_reports_not_loaded():
    engine = _FakeEngine()
    engine._models = None
    snap = collect(engine, query_devices=False)
    assert snap.stt_device == "not loaded"


def test_last_turn_metrics_are_surfaced():
    engine = _FakeEngine(last_turn={
        "turn_end_reason": "silence", "captured_audio_duration_s": 1.25,
        "vad_prob_max": 0.98, "vad_prob_mean": 0.61, "stt_s": 0.33,
        "stt_had_text": False,
    })
    snap = collect(engine, query_devices=False)
    assert snap.last_turn_end_reason == "silence"
    assert snap.last_captured_audio_s == pytest.approx(1.25)
    assert snap.last_vad_prob_max == pytest.approx(0.98)
    assert snap.last_vad_prob_mean == pytest.approx(0.61)
    assert snap.last_stt_s == pytest.approx(0.33)
    assert snap.last_stt_had_text is False


def test_missing_counters_report_unknown_not_zero():
    """WavAudioIO/RemoteWsAudioIO don't have every counter DuplexAudioIO
    does. Reporting a missing counter as 0 would read as "measured, and
    fine" -- exactly the false-clean reading this codebase keeps having to
    stamp out."""
    class _Bare:
        _sample_rate = 16000

    snap = collect(_FakeEngine(audio_io=_Bare()), query_devices=False)
    assert snap.queue_depth is None
    assert snap.input_status_count is None
    assert snap.input_overflow_count is None
    assert snap.output_underrun_count is None


def test_collect_never_raises_on_a_half_initialized_engine():
    """A diagnostics read must never be the thing that breaks voice."""
    snap = collect(SimpleNamespace(), query_devices=False)  # no _audio_io at all
    assert isinstance(snap, VoiceDiagnosticsSnapshot)
    assert snap.mic_rms == 0.0


def test_state_is_optional():
    snap = collect(_FakeEngine(), query_devices=False)
    assert snap.capture is None and snap.display is None


# ── The registry ────────────────────────────────────────────────────────────

def test_no_session_is_an_answer_not_an_error():
    snap = current_snapshot(query_devices=False)
    assert snap.session_active is False
    assert snap.notes


def test_a_registered_session_is_reported_as_active():
    register_session(_FakeEngine(), VoiceState())
    snap = current_snapshot(query_devices=False)
    assert snap.session_active is True
    assert snap.sample_rate == 16000


def test_register_returns_the_displaced_session_so_it_can_be_restored():
    """A remote /ws session takes over reporting while it runs and must hand
    it back to the local loop -- which is still running underneath, merely
    paused, so clearing instead of restoring would claim no session exists
    while one very much does."""
    local = _FakeEngine(stt_device="local-gpu")
    register_session(local, VoiceState())

    remote = _FakeEngine(stt_device="remote-gpu")
    previous = register_session(remote, VoiceState())
    assert current_snapshot(query_devices=False).stt_device == "remote-gpu"

    restore_session(previous)
    assert current_snapshot(query_devices=False).stt_device == "local-gpu"


def test_snapshot_is_json_serializable():
    import json

    register_session(_FakeEngine(), VoiceState())
    json.dumps(current_snapshot(query_devices=False).to_dict())  # must not raise


# ── The CLI surface ─────────────────────────────────────────────────────────

def test_cli_voice_status_prints_the_live_counters(capsys):
    from jarvis import cli

    state = VoiceState()
    state.set_capture("listening")
    cli._print_voice_status(_FakeEngine(last_turn={
        "turn_end_reason": "silence", "captured_audio_duration_s": 1.25,
        "vad_prob_max": 0.98, "vad_prob_mean": 0.61, "stt_s": 0.33,
    }), state)

    out = capsys.readouterr().out
    for expected in ("cuda", "listening", "silence"):
        assert expected in out, out


def test_cli_voice_status_never_crashes_without_a_session(capsys):
    from jarvis import cli

    cli._print_voice_status(SimpleNamespace())  # must not raise
    assert capsys.readouterr().out


# ── The API surface ─────────────────────────────────────────────────────────

def test_voice_status_endpoint_requires_auth():
    from fastapi.testclient import TestClient
    from jarvis import api
    from jarvis.config import Settings

    original = api._settings
    api._settings = Settings(_env_file=None, jarvis_api_key="secret-key")
    try:
        client = TestClient(api.app)
        assert client.get("/voice/status").status_code == 401
        ok = client.get("/voice/status", headers={"X-API-Key": "secret-key"})
        assert ok.status_code == 200
        assert ok.json()["session_active"] is False
    finally:
        api._settings = original


def test_voice_status_endpoint_reports_a_live_session():
    from fastapi.testclient import TestClient
    from jarvis import api
    from jarvis.config import Settings

    original = api._settings
    api._settings = Settings(_env_file=None, jarvis_api_key="secret-key")
    state = VoiceState()
    state.set_capture("listening")
    state.set_response("speaking")
    register_session(_FakeEngine(), state)
    try:
        client = TestClient(api.app)
        body = client.get("/voice/status", headers={"X-API-Key": "secret-key"}).json()
        assert body["session_active"] is True
        assert body["queue_depth"] == 7
        assert body["input_status_count"] == 3
        assert body["input_overflow_count"] == 1
        assert body["capture_callback_count"] == 11
        assert body["capture_consumed_frame_count"] == 10
        assert body["capture_queue_high_watermark"] == 2
        assert body["vad_frame_count"] == 10
        assert body["speech_started_count"] == 1
        assert body["turn_ended_count"] == 1
        assert body["stt_attempt_count"] == 1
        assert body["stt_nonempty_count"] == 1
        assert body["output_underrun_count"] == 2
        assert body["stt_device"] == "cuda"
        assert body["display"] == "speaking"
        assert body["hud_state"] == "speaking"
    finally:
        api._settings = original
