"""Orchestration-owned voice session state — Faz D (voice observability).

Deliberately NOT a single enum and NOT owned by RealtimeVoiceEngine. Two
independent axes because full-duplex audio makes them genuinely
simultaneous: the mic stays open while JARVIS speaks (that's the entire
mechanism barge-in depends on), so LISTENING and SPEAKING can both be true
at once. RealtimeVoiceEngine stays transport-blind and yields only
low-level events (SpeechStarted/TurnEnded/FinalTranscript/BargeIn/MicLevel)
— it has no notion of an agent turn, confirmation, or TTS. THINKING and
AWAITING_CONFIRMATION are pure orchestration concepts (owner's explicit
architectural correction during plan review): they live here, driven by
jarvis/voice/session.py's drive_voice_session()/arm_and_speak_confirmation()/
resolve_confirmation() and each transport's own turn-response function
(cli.py's _run_voice_response(), voice_api.py's run_one_response()), never
inside the engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal

CaptureState = Literal["idle", "listening", "speech_detected", "transcribing"]
ResponseState = Literal["idle", "thinking", "awaiting_confirmation", "speaking"]

# The single derived label a UI actually renders, when both axes are
# simultaneously non-idle. Priority order per the approved plan: a pending
# confirmation question is the most important thing to show (it's blocking
# on the user), then active capture progress, then the response's own
# progress, then a bare "still listening" baseline.
_DISPLAY_PRIORITY: tuple[str, ...] = (
    "awaiting_confirmation", "transcribing", "speech_detected",
    "thinking", "speaking", "listening",
)


@dataclass
class VoiceState:
    """Mutable, single-session state holder. One instance per voice session
    (constructed once by the CLI/API loop, threaded into drive_voice_session()
    and every function that calls speak_stream()/arms a confirmation).

    on_change fires after EITHER axis actually changes (not on a same-value
    set) so a renderer can do a single "redraw the status line" callback
    instead of polling."""
    capture: CaptureState = "idle"
    response: ResponseState = "idle"
    on_change: Callable[["VoiceState"], None] | None = field(default=None, repr=False, compare=False)

    def set_capture(self, value: CaptureState) -> None:
        if value == self.capture:
            return
        self.capture = value
        self._notify()

    def set_response(self, value: ResponseState) -> None:
        if value == self.response:
            return
        self.response = value
        self._notify()

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change(self)

    @property
    def display(self) -> str:
        active = {self.capture, self.response} - {"idle"}
        for label in _DISPLAY_PRIORITY:
            if label in active:
                return label
        return "idle"
