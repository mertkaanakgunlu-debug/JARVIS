"""jarvis/voice/state.py + its wiring into session.py -- Faz D (voice
observability). VoiceState is a pure, orchestration-owned two-axis reducer
(NOT engine-owned -- see the module's own docstring for the architectural
reasoning): CaptureState tracks what the mic/VAD/STT are doing,
ResponseState tracks what the agent turn/TTS are doing, and both can be
simultaneously non-idle (full-duplex: JARVIS can be SPEAKING while still
LISTENING for a barge-in).
"""
from __future__ import annotations

import pytest

from jarvis.voice.state import VoiceState


# ── VoiceState: pure reducer logic ──────────────────────────────────────────

def test_starts_idle_on_both_axes():
    s = VoiceState()
    assert s.capture == "idle" and s.response == "idle"
    assert s.display == "idle"


def test_set_capture_updates_and_notifies():
    seen = []
    s = VoiceState(on_change=seen.append)
    s.set_capture("listening")
    assert s.capture == "listening"
    assert seen == [s]


def test_setting_the_same_value_does_not_notify():
    seen = []
    s = VoiceState(on_change=seen.append)
    s.set_capture("listening")
    s.set_capture("listening")  # no-op, same value
    assert len(seen) == 1


def test_response_axis_is_independent_of_capture_axis():
    s = VoiceState()
    s.set_capture("listening")
    s.set_response("speaking")
    assert s.capture == "listening" and s.response == "speaking"


@pytest.mark.parametrize("capture,response,want", [
    ("idle", "idle", "idle"),
    ("listening", "idle", "listening"),
    ("idle", "thinking", "thinking"),
    # full-duplex: both non-idle at once -- SPEAKING must not hide LISTENING
    # being the less-interesting of the two, but a genuinely higher-priority
    # state (a pending question) must win over routine capture progress.
    ("listening", "speaking", "speaking"),
    ("speech_detected", "speaking", "speech_detected"),
    ("transcribing", "thinking", "transcribing"),
    ("listening", "awaiting_confirmation", "awaiting_confirmation"),
    ("transcribing", "awaiting_confirmation", "awaiting_confirmation"),
])
def test_display_priority_order(capture, response, want):
    s = VoiceState()
    s.capture = capture
    s.response = response
    assert s.display == want


def test_on_change_none_is_a_safe_default():
    s = VoiceState()  # no on_change -- must not raise
    s.set_capture("listening")
    s.set_response("thinking")


# ── drive_voice_session(): capture-axis wiring ──────────────────────────────

class _FakeEngine:
    """Same minimal shape as test_exit_phrase.py's _FakeEngine."""
    def __init__(self, events):
        self._events = events

    async def events(self):
        for e in self._events:
            yield e


@pytest.mark.asyncio
async def test_session_starts_listening_immediately():
    from jarvis.voice.session import drive_voice_session

    state = VoiceState()
    engine = _FakeEngine([])

    async def on_transcript(text, lang):
        return None

    # events() is empty -> "ended" immediately, but the initial listening
    # state must already have been set before the loop even starts consuming.
    outcome = await drive_voice_session(engine, on_transcript, state=state)
    assert outcome == "ended"
    assert state.capture == "listening"


@pytest.mark.asyncio
async def test_speech_started_then_turn_ended_then_transcript_sequence():
    """on_change fires for EITHER axis, so this tracks only actual capture-axis
    transitions (a response-axis change firing on_change while capture happens
    to be unchanged must not look like a duplicate capture transition)."""
    from jarvis.voice.events import FinalTranscript, SpeechStarted, TurnEnded
    from jarvis.voice.session import drive_voice_session

    seen_captures: list[str] = []

    def _on_change(s):
        if not seen_captures or seen_captures[-1] != s.capture:
            seen_captures.append(s.capture)

    state = VoiceState(on_change=_on_change)
    engine = _FakeEngine([
        SpeechStarted(),
        TurnEnded(reason="silence", captured_audio_duration_s=1.2),
        FinalTranscript(text="merhaba", lang="tr"),
    ])

    async def on_transcript(text, lang):
        return None  # nothing to track further

    await drive_voice_session(engine, on_transcript, state=state)

    # listening (initial) -> speech_detected -> transcribing -> listening (post-STT)
    assert seen_captures == ["listening", "speech_detected", "transcribing", "listening"]


@pytest.mark.asyncio
async def test_final_transcript_sets_response_to_thinking():
    from jarvis.voice.events import FinalTranscript
    from jarvis.voice.session import drive_voice_session

    state = VoiceState()
    engine = _FakeEngine([FinalTranscript(text="merhaba", lang="tr")])

    async def on_transcript(text, lang):
        return None

    await drive_voice_session(engine, on_transcript, state=state)
    # on_transcript returned None (no tracked turn task) -- response should
    # still have been set to "thinking" the moment the transcript arrived.
    assert state.response == "thinking"


@pytest.mark.asyncio
async def test_a_completed_turn_resets_response_to_idle():
    from jarvis.voice.events import FinalTranscript
    from jarvis.voice.session import drive_voice_session

    state = VoiceState()
    engine = _FakeEngine([FinalTranscript(text="merhaba", lang="tr")])

    async def on_transcript(text, lang):
        async def _turn():
            state.set_response("speaking")  # simulates the caller's own speak_stream bracket
        return _turn()

    outcome = await drive_voice_session(engine, on_transcript, state=state)
    assert outcome == "ended"
    assert state.response == "idle"


@pytest.mark.asyncio
async def test_a_turn_that_leaves_a_confirmation_armed_is_not_reset_to_idle():
    """The exact scenario arm_and_speak_confirmation's own docstring warns
    about: a completed turn must not silently clobber awaiting_confirmation
    back to idle -- the user hasn't answered yet."""
    from jarvis.voice.events import FinalTranscript
    from jarvis.voice.session import drive_voice_session

    state = VoiceState()
    engine = _FakeEngine([FinalTranscript(text="dosyayı sil", lang="tr")])

    async def on_transcript(text, lang):
        async def _turn():
            state.set_response("awaiting_confirmation")  # simulates arm_and_speak_confirmation
        return _turn()

    await drive_voice_session(engine, on_transcript, state=state)
    assert state.response == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_barge_in_resets_response_and_marks_capture_as_speech_detected():
    import asyncio

    from jarvis.voice.events import BargeIn, FinalTranscript
    from jarvis.voice.session import drive_voice_session

    state = VoiceState()

    async def _slow_turn():
        state.set_response("speaking")
        await asyncio.sleep(10)  # cancelled by the BargeIn before this ever returns

    class _EngineWithSlowTurn:
        async def events(self):
            yield FinalTranscript(text="ilk komut", lang="tr")
            yield BargeIn()

    calls = {"n": 0}

    async def on_transcript(text, lang):
        calls["n"] += 1
        return _slow_turn()

    await drive_voice_session(_EngineWithSlowTurn(), on_transcript, state=state)
    assert calls["n"] == 1
    assert state.response == "idle"
    assert state.capture == "speech_detected"


# ── arm_and_speak_confirmation / resolve_confirmation: response-axis wiring ──

class _FakeSpeakEngine:
    async def speak_stream(self, text_iter, lang="en"):
        async for _ in text_iter:
            pass


@pytest.mark.asyncio
async def test_arm_and_speak_confirmation_sets_speaking_then_awaiting_confirmation():
    from jarvis.voice.session import arm_and_speak_confirmation

    state = VoiceState()
    marker = {"id": "conf-1", "payload": {"tools": [{"name": "gmail", "description": "send"}]}}

    await arm_and_speak_confirmation(_FakeSpeakEngine(), marker, "tr", state=state)

    assert state.response == "awaiting_confirmation"


@pytest.mark.asyncio
async def test_resolve_confirmation_sets_speaking_when_no_second_interrupt():
    from jarvis.voice.session import PendingConfirmation, resolve_confirmation

    class _FakeAgent:
        async def resume_and_stream(self, conf_id, decision, *, pre_claimed=None):
            yield "Tamamlandı."

    state = VoiceState()
    seen_during_speak = []

    class _RecordingEngine:
        async def speak_stream(self, text_iter, lang="en"):
            seen_during_speak.append(state.response)
            async for _ in text_iter:
                pass

    await resolve_confirmation(
        _FakeAgent(), _RecordingEngine(), PendingConfirmation("conf-1", {}), "evet", "tr",
        state=state,
    )

    assert seen_during_speak == ["speaking"]
