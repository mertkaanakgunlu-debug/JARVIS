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


# ── hud_state: the collapse onto the published WS wire vocabulary ───────────

@pytest.mark.parametrize("capture,response,want", [
    ("idle", "idle", "idle"),
    ("listening", "idle", "listening"),
    ("speech_detected", "idle", "listening"),      # still just "mic open" to a viewer
    ("transcribing", "idle", "thinking"),          # STT is work, not capture
    ("idle", "thinking", "thinking"),
    ("listening", "speaking", "speaking"),
    ("listening", "awaiting_confirmation", "listening"),  # mic open for the yes/no
])
def test_hud_state_collapses_display_onto_the_wire_vocabulary(capture, response, want):
    """electron/src/renderer/src/hooks/useJarvisSocket.js documents exactly
    {"listening"|"speaking"|"thinking"|"working"|"idle"} -- the reducer tracks
    more than that, so anything it emits must land inside that set."""
    s = VoiceState()
    s.capture = capture
    s.response = response
    assert s.hud_state == want


def test_hud_state_never_emits_a_value_the_hud_cannot_render():
    from jarvis.voice.state import CaptureState, ResponseState
    import typing

    renderable = {"listening", "speaking", "thinking", "working", "idle"}
    s = VoiceState()
    for capture in typing.get_args(CaptureState):
        for response in typing.get_args(ResponseState):
            s.capture, s.response = capture, response
            assert s.hud_state in renderable, (capture, response, s.hud_state)


# ── hud_state_emitter: one wire frame per REAL change ──────────────────────

def test_emitter_publishes_each_distinct_hud_state():
    from jarvis.voice.state import hud_state_emitter

    seen: list[str] = []
    s = VoiceState()
    s.on_change = hud_state_emitter(seen.append)

    s.set_capture("listening")
    s.set_response("thinking")
    s.set_response("speaking")
    s.set_capture("idle")
    s.set_response("idle")

    assert seen == ["listening", "thinking", "speaking", "idle"]


def test_emitter_suppresses_repeats_that_collapse_to_the_same_wire_value():
    """listening -> speech_detected is a real reducer transition but the same
    wire value; without the dedupe every utterance would spray duplicate
    {"type":"state"} frames at every connected HUD client."""
    from jarvis.voice.state import hud_state_emitter

    seen: list[str] = []
    s = VoiceState()
    s.on_change = hud_state_emitter(seen.append)

    s.set_capture("listening")
    s.set_capture("speech_detected")   # -> still "listening"
    s.set_capture("transcribing")      # -> "thinking"
    s.set_response("thinking")         # -> still "thinking"

    assert seen == ["listening", "thinking"]


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

    seen: list[str] = []
    state = VoiceState(on_change=lambda s: seen.append(s.capture))
    engine = _FakeEngine([])

    async def on_transcript(text, lang):
        return None

    # events() is empty -> "ended" immediately, but the initial listening
    # state must already have been set before the loop even starts consuming.
    outcome = await drive_voice_session(engine, on_transcript, state=state)
    assert outcome == "ended"
    assert seen[0] == "listening"


@pytest.mark.asyncio
async def test_session_exit_returns_capture_to_idle():
    """Review remediation (2026-07-25): capture was set to "listening" on
    entry and never cleared, so after the session returned -- and every
    caller then calls engine.stop() -- the reducer still claimed a mic that
    is physically closed. Most visible at the --ptt/--wakeword gate, which
    sits BETWEEN drive_voice_session() calls waiting for Enter / the wake
    phrase; it must read idle there, not listening."""
    from jarvis.voice.session import drive_voice_session

    state = VoiceState()
    engine = _FakeEngine([])

    async def on_transcript(text, lang):
        return None

    await drive_voice_session(engine, on_transcript, state=state)
    assert state.capture == "idle"
    assert state.response == "idle"
    assert state.display == "idle"


@pytest.mark.asyncio
async def test_session_exit_preserves_an_armed_confirmation():
    """The one response state that legitimately outlives the session: PTT/
    wakeword mode returns "turn_complete" between turns while the question
    is still unanswered, and the caller's pending_confirmation survives
    across drive_voice_session() calls to be resolved by the next utterance.
    Capture still goes idle (the mic really is closed); response must not."""
    from jarvis.voice.events import FinalTranscript
    from jarvis.voice.session import drive_voice_session

    state = VoiceState()
    engine = _FakeEngine([FinalTranscript(text="dosyayı sil", lang="tr")])

    async def on_transcript(text, lang):
        async def _turn():
            state.set_response("awaiting_confirmation")
        return _turn()

    await drive_voice_session(engine, on_transcript, state=state)
    assert state.capture == "idle"
    assert state.response == "awaiting_confirmation"
    assert state.display == "awaiting_confirmation"


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

    # listening (initial) -> speech_detected -> transcribing -> listening
    # (post-STT) -> idle (session teardown; see the finally block)
    assert seen_captures == [
        "listening", "speech_detected", "transcribing", "listening", "idle",
    ]


@pytest.mark.asyncio
async def test_final_transcript_sets_response_to_thinking():
    from jarvis.voice.events import FinalTranscript
    from jarvis.voice.session import drive_voice_session

    state = VoiceState()
    engine = _FakeEngine([FinalTranscript(text="merhaba", lang="tr")])
    seen_inside: list[str] = []

    async def on_transcript(text, lang):
        # response must already be "thinking" by the time the callback that
        # handles the transcript is invoked -- set the moment it arrived.
        seen_inside.append(state.response)
        return None

    await drive_voice_session(engine, on_transcript, state=state)
    assert seen_inside == ["thinking"]
    # ...and a None result means that turn is ALREADY over (the callback
    # finished it synchronously), so "thinking" must not be left behind.
    assert state.response == "idle"


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
    seen_captures: list[str] = []
    state.on_change = lambda s: seen_captures.append(s.capture)

    async def on_transcript(text, lang):
        calls["n"] += 1
        return _slow_turn()

    await drive_voice_session(_EngineWithSlowTurn(), on_transcript, state=state)
    assert calls["n"] == 1
    # The barge-in itself IS new speech -- capture must flip to
    # speech_detected while the session is still running...
    assert "speech_detected" in seen_captures
    # ...but the session then ends, so both axes settle back to idle rather
    # than leaving a closed mic reported as mid-utterance.
    assert state.response == "idle"
    assert state.capture == "idle"


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
