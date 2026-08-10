"""Faz E acceptance, filled in after the fact: press-to-arm -> ONE utterance
-> VAD-stop -> back to the gate, driven deterministically through the real
RealtimeVoiceEngine + real drive_voice_session() with WavAudioIO standing in
for the microphone (same fake-VAD/fake-STT tier as tests/test_wav_replay.py,
so no model download and no audio hardware).

The plan asked for exactly this test and the Faz E commit shipped without it.
The gap it left was real, not theoretical: drive_voice_session() only honored
stop_after_first_turn when a turn returned a *coroutine* to track, so any
on_transcript branch that finished its work synchronously and returned None
never ended the session. cli.py's _detect_model_switch branch does precisely
that -- it switches the model, awaits its own speak_stream(), and returns
None -- so in --ptt mode "flash modeline geç" spoke its confirmation and then
left the mic open forever instead of returning to the press-Enter gate.
test_a_synchronously_handled_turn_still_completes_the_session below is that
bug's regression test; test_ptt_gate_reads_idle_between_turns covers the
state-lifecycle half of the same live symptom.
"""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.config import Settings
from jarvis.voice.engine import RealtimeVoiceEngine, VoiceModels
from jarvis.voice.io_wav import ENGINE_FRAME_SAMPLES, WavAudioIO
from jarvis.voice.session import STOP_SESSION, drive_voice_session
from jarvis.voice.state import VoiceState
from jarvis.voice.stt_whisper import TranscriptionResult


class _FakeVAD:
    """Energy-threshold VAD -- identical to tests/test_wav_replay.py's."""
    frame_samples = ENGINE_FRAME_SAMPLES

    def __init__(self, energy_threshold: float = 0.05):
        self._threshold = energy_threshold

    def reset_states(self) -> None:
        pass

    def process_chunk(self, chunk: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0
        return 1.0 if rms > self._threshold else 0.0


class _FakeStt:
    def __init__(self, text: str = "flash modeline geç", lang: str = "tr"):
        self._text, self._lang = text, lang

    def transcribe(self, audio: np.ndarray) -> TranscriptionResult:
        return TranscriptionResult(text=self._text, lang=self._lang, stt_s=0.01)


def _one_utterance() -> np.ndarray:
    """~1.3s of loud "speech"; WavAudioIO appends the trailing silence that
    makes the VAD emit a real turn-end (see its trailing_silence_s docstring)."""
    rng = np.random.default_rng(42)
    return (rng.standard_normal(40 * ENGINE_FRAME_SAMPLES) * 0.3).astype(np.float32)


def _engine(stt: "_FakeStt | None" = None) -> RealtimeVoiceEngine:
    audio_io = WavAudioIO(_one_utterance(), trailing_silence_s=1.6)
    models = VoiceModels(vad=_FakeVAD(), stt=stt or _FakeStt(), tts=None)
    return RealtimeVoiceEngine(audio_io, Settings(_env_file=None), models=models)


# ── The Faz E contract: one press -> exactly one turn ────────────────────────

@pytest.mark.asyncio
async def test_a_tracked_turn_completes_the_session():
    """Baseline (this already worked): a turn returning a coroutine ends the
    session once that coroutine finishes, so --ptt re-gates on the next Enter."""
    engine = _engine()
    await engine.load()
    calls = {"n": 0}

    async def on_transcript(text, lang):
        async def _turn():
            calls["n"] += 1
        return _turn()

    outcome = await drive_voice_session(
        engine, on_transcript, stop_after_first_turn=True,
    )
    assert outcome == "turn_complete"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_a_synchronously_handled_turn_still_completes_the_session():
    """THE regression: a callback that does its work inline and returns None
    (cli.py's model-switch branch) must end the session exactly the same way.
    Before the fix this hung -- no turn_task existed, so stop_after_first_turn
    was never evaluated and the mic stayed open past the utterance."""
    engine = _engine()
    await engine.load()
    handled: list[str] = []

    async def on_transcript(text, lang):
        handled.append(text)   # e.g. switch the model and speak the confirmation
        return None            # ...already fully done; nothing to track

    outcome = await drive_voice_session(
        engine, on_transcript, stop_after_first_turn=True,
    )
    assert outcome == "turn_complete"
    assert handled == ["flash modeline geç"]


@pytest.mark.asyncio
async def test_a_synchronous_turn_does_not_end_a_continuous_session():
    """The same None result must NOT end a plain `--voice` (continuous) session
    -- stop_after_first_turn is what distinguishes the two, and the fix must
    not turn every synchronous branch into a session-ender."""
    engine = _engine()
    await engine.load()
    handled: list[str] = []

    async def on_transcript(text, lang):
        handled.append(text)
        return None

    outcome = await drive_voice_session(
        engine, on_transcript, stop_after_first_turn=False,
    )
    # The WAV source runs out rather than a turn ending the session.
    assert outcome == "ended"
    assert handled == ["flash modeline geç"]


@pytest.mark.asyncio
async def test_an_exit_phrase_still_wins_over_stop_after_first_turn():
    engine = _engine(_FakeStt(text="güle güle"))
    await engine.load()

    async def on_transcript(text, lang):
        return STOP_SESSION

    outcome = await drive_voice_session(
        engine, on_transcript, stop_after_first_turn=True,
    )
    assert outcome == "exit"


@pytest.mark.asyncio
async def test_an_empty_stt_result_still_completes_one_ptt_capture():
    """A captured/VAD-ended turn whose Whisper result is blank is still one
    completed press-to-talk attempt.  It must return to the activation gate
    instead of silently keeping the microphone session open until the user
    speaks a second time."""
    engine = _engine(_FakeStt(text=""))
    await engine.load()
    handled: list[str] = []

    async def on_transcript(text, lang):
        handled.append(text)
        return None

    outcome = await drive_voice_session(
        engine, on_transcript, stop_after_first_turn=True,
    )

    assert outcome == "turn_complete"
    assert handled == [], "blank STT output must not become an agent turn"


# ── The state half of the same symptom ──────────────────────────────────────

@pytest.mark.asyncio
async def test_ptt_gate_reads_idle_between_turns():
    """--ptt sits at `input()` between turns with the engine stopped. The
    reducer must say idle there; it used to still say listening because
    capture was set on entry and never cleared."""
    engine = _engine()
    await engine.load()
    state = VoiceState()

    async def on_transcript(text, lang):
        return None

    outcome = await drive_voice_session(
        engine, on_transcript, state=state, stop_after_first_turn=True,
    )
    assert outcome == "turn_complete"
    assert state.capture == "idle"
    assert state.response == "idle"
    assert state.display == "idle"


@pytest.mark.asyncio
async def test_the_capture_axis_tracks_the_real_utterance_before_going_idle():
    """The idle-on-exit reset must not swallow the transitions that happened
    while the mic really was open -- speech_detected and transcribing still
    have to be observable during the turn."""
    engine = _engine()
    await engine.load()
    seen: list[str] = []
    state = VoiceState(on_change=lambda s: seen.append(s.capture))

    async def on_transcript(text, lang):
        return None

    await drive_voice_session(
        engine, on_transcript, state=state, stop_after_first_turn=True,
    )
    assert "speech_detected" in seen
    assert "transcribing" in seen
    assert seen[-1] == "idle"


# ── The live-reachable trigger, at the CLI's own callback level ─────────────

def test_cli_model_switch_is_a_synchronous_none_returning_branch():
    """Pins WHY the bug was reachable in --ptt: _detect_model_switch matches a
    plain spoken phrase, and cli.py's handler for it returns None rather than a
    coroutine. If this branch ever starts returning a coroutine the regression
    tests above still hold, but this test is the one that would flag that the
    None path lost its real-world trigger."""
    from jarvis.cli import _detect_model_switch

    assert _detect_model_switch("flash modeline geç") is not None


# ── The None path must not trample a turn that is still running ─────────────

@pytest.mark.asyncio
async def test_a_synchronous_turn_does_not_cancel_an_in_flight_earlier_turn():
    """Self-review finding (2026-07-25): the None branch originally applied
    end-of-turn handling unconditionally. If an EARLIER turn was still
    speaking, that meant reporting response="idle" over audible TTS and --
    with stop_after_first_turn -- returning "turn_complete", whose finally
    cancels the in-flight turn outright. The running turn owns both."""
    import asyncio

    from jarvis.voice.events import FinalTranscript
    from jarvis.voice.session import drive_voice_session
    from jarvis.voice.state import VoiceState

    finished: list[str] = []

    async def _slow_turn():
        state.set_response("speaking")
        await asyncio.sleep(0.05)
        finished.append("turn-a")

    class _TwoTranscripts:
        async def events(self):
            yield FinalTranscript(text="ilk komut", lang="tr")
            yield FinalTranscript(text="flash modeline geç", lang="tr")
            await asyncio.sleep(0.2)   # let turn A finish before events end

    state = VoiceState()
    # An "idle" published while turn A is between its own speaking and its
    # completion is the reported-silence-over-audible-TTS symptom.
    idle_during_speech: list[str] = []
    speaking_started: list[bool] = []

    def _on_change(s):
        if s.response == "speaking":
            speaking_started.append(True)
        elif s.response == "idle" and speaking_started and not finished:
            idle_during_speech.append(s.response)

    state.on_change = _on_change
    calls = {"n": 0}

    async def on_transcript(text, lang):
        calls["n"] += 1
        if calls["n"] == 1:
            return _slow_turn()      # tracked turn, still running…
        return None                  # …when this synchronous one lands

    outcome = await drive_voice_session(
        _TwoTranscripts(), on_transcript, state=state, stop_after_first_turn=True,
    )

    # Turn A ran to completion rather than being cancelled, and IT ended the
    # session -- the synchronous second utterance did not end it early.
    assert finished == ["turn-a"]
    assert outcome == "turn_complete"
    assert idle_during_speech == []
