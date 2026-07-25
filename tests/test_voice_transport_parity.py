"""All three voice transports drive the HUD from ONE reducer -- the
transport-parity review finding (2026-07-25).

Faz D built jarvis/voice/state.py's VoiceState but wired it only into
cli.py's --voice loop. jarvis/voice_api.py (the local wakeword/PTT loop) and
jarvis/api.py's remote /ws audio session kept publishing hand-picked
event_bus.state() literals at each call site: two state contracts for the
same state machine, with the reducer's priority rules (both axes live at
once, awaiting_confirmation outranking routine progress) reaching only the
CLI. These tests pin the wiring itself, so a future edit can't quietly drop
a transport back to ad-hoc literals.
"""
from __future__ import annotations

import inspect

import pytest

from jarvis.voice.state import VoiceState, hud_state_emitter


# ── run_one_response(): reducer-driven, not literal-driven ──────────────────

class _FakeEngine:
    async def speak_stream(self, text_iter, lang="en"):
        async for _ in text_iter:
            pass


class _FakeAgent:
    def __init__(self, tokens=("Tamam", ", efendim.")):
        self._tokens = tokens

    async def chat_stream(self, text, detected_language=None, transport=None):
        for t in self._tokens:
            yield t


@pytest.mark.asyncio
async def test_run_one_response_moves_the_reducer_not_the_bus(monkeypatch):
    from jarvis import voice_api

    bus_calls: list[str] = []
    monkeypatch.setattr(voice_api.event_bus, "state", bus_calls.append)
    monkeypatch.setattr(voice_api.event_bus, "message", lambda who, text: None)

    state = VoiceState()
    seen: list[str] = []
    state.on_change = lambda s: seen.append(s.response)

    await voice_api.run_one_response(
        _FakeAgent(), _FakeEngine(), "merhaba", "tr", state=state,
    )

    assert seen == ["thinking", "speaking"]
    # With a reducer wired, the transport must NOT also publish its own
    # literals -- that duplication is exactly what drifted apart before.
    assert bus_calls == []


@pytest.mark.asyncio
async def test_run_one_response_without_a_reducer_still_publishes_state(monkeypatch):
    """The fallback exists so a not-yet-wired caller goes on producing wire
    frames rather than silently going dark."""
    from jarvis import voice_api

    bus_calls: list[str] = []
    monkeypatch.setattr(voice_api.event_bus, "state", bus_calls.append)
    monkeypatch.setattr(voice_api.event_bus, "message", lambda who, text: None)

    await voice_api.run_one_response(_FakeAgent(), _FakeEngine(), "merhaba", "tr")

    assert bus_calls == ["thinking", "speaking"]


@pytest.mark.asyncio
async def test_a_full_turn_emits_the_wire_frames_the_hud_expects():
    """The reducer transitions one real turn makes, in the order
    drive_voice_session() actually makes them, collapsed to the wire."""
    emitted: list[str] = []
    state = VoiceState(on_change=hud_state_emitter(emitted.append))

    state.set_capture("listening")          # session start
    state.set_capture("speech_detected")    # user starts talking   -> listening
    state.set_capture("transcribing")       # VAD turn-end, STT      -> thinking
    state.set_response("thinking")          # transcript in hand    (response first)
    state.set_capture("listening")          # capture back to baseline
    state.set_response("speaking")          # TTS
    state.set_response("idle")              # turn done              -> listening
    state.set_capture("idle")               # session teardown       -> idle

    assert emitted == ["listening", "thinking", "speaking", "listening", "idle"]


@pytest.mark.asyncio
async def test_transcription_to_thinking_does_not_flicker_through_listening():
    """Regression on the axis ORDER inside drive_voice_session()'s
    FinalTranscript branch. Setting capture back to its baseline before
    setting response passes through (listening, idle) for one notification,
    which the collapse publishes as a "listening" frame wedged between the
    STT "thinking" and the turn's own "thinking" -- a visible one-frame orb
    flicker the instant transcription completes. Response-first avoids it."""
    from jarvis.voice.events import FinalTranscript
    from jarvis.voice.session import drive_voice_session

    from jarvis.voice.events import SpeechStarted, TurnEnded

    class _FakeEventEngine:
        async def events(self):
            yield SpeechStarted()
            yield TurnEnded(reason="silence", captured_audio_duration_s=1.2)
            yield FinalTranscript(text="merhaba", lang="tr")

    emitted: list[str] = []
    state = VoiceState(on_change=hud_state_emitter(emitted.append))

    async def on_transcript(text, lang):
        async def _turn():
            state.set_response("speaking")
        return _turn()

    await drive_voice_session(_FakeEventEngine(), on_transcript, state=state)

    # thinking -> speaking directly. Capture-first ordering would wedge an
    # extra "listening" between them (STT done, turn not yet marked), i.e.
    # ["listening", "thinking", "listening", "thinking", "speaking", ...].
    assert emitted == ["listening", "thinking", "speaking", "listening", "idle"]


# ── The wiring itself ───────────────────────────────────────────────────────

def _source_of(func) -> str:
    return inspect.getsource(func)


def test_local_voice_loop_constructs_and_threads_one_reducer():
    from jarvis import voice_api

    src = _source_of(voice_api._voice_loop)
    assert "VoiceState(on_change=hud_state_emitter(event_bus.state))" in src
    assert "state=voice_state" in src


def test_remote_ws_session_constructs_and_threads_one_reducer():
    """jarvis/api.py's /ws audio session -- the third transport."""
    from jarvis import api

    src = inspect.getsource(api)
    assert "VoiceState(on_change=hud_state_emitter(event_bus.state))" in src
    assert "drive_voice_session(engine, _handle_transcript, state=voice_state)" in src


def test_the_local_loop_no_longer_brackets_sessions_with_manual_state_frames():
    """The reducer owns listening/idle around a session now. A stray manual
    frame here would race the reducer's own and reintroduce the drift."""
    from jarvis import voice_api

    src = _source_of(voice_api._voice_loop)
    # Still allowed OUTSIDE a session: the pre-loop idle and the error/cancel
    # paths. Not allowed: the listening frame that used to bracket
    # drive_voice_session().
    assert 'event_bus.state("listening")' not in src
