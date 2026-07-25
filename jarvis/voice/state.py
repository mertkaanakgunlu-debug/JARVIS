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

# The HUD/WS wire vocabulary is narrower than this reducer's own, and is a
# published contract: electron/src/renderer/src/hooks/useJarvisSocket.js
# documents {type:"state", value:"listening"|"speaking"|"thinking"|"working"
# |"idle"}, and the renderer's orb animations switch on exactly those
# strings. So the collapse to it happens HERE, in one table, rather than
# each transport hand-picking its own event_bus.state() literals at each
# call site (what voice_api.py/api.py did before this reducer reached them
# -- the drift the transport-parity review finding was about).
#
# awaiting_confirmation maps to "listening" deliberately: the mic really is
# open for the user's spoken yes/no. The HUD renders the question itself
# through its own confirmation overlay (the structured SSE frame + WS
# broadcast), not through the orb's state, so nothing is lost by collapsing
# it here -- and widening the wire vocabulary would need a matching Electron
# change that no live HUD E2E has covered yet.
_HUD_STATE: dict[str, str] = {
    "idle": "idle",
    "listening": "listening",
    "speech_detected": "listening",   # mic open, user mid-utterance
    "transcribing": "thinking",       # STT running -- work, not capture, to a viewer
    "thinking": "thinking",
    "speaking": "speaking",
    "awaiting_confirmation": "listening",
}


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

    @property
    def hud_state(self) -> str:
        """display, collapsed onto the HUD/WS wire vocabulary (_HUD_STATE)."""
        return _HUD_STATE.get(self.display, "idle")


def hud_state_emitter(emit: Callable[[str], None]) -> Callable[["VoiceState"], None]:
    """VoiceState.on_change callback that forwards hud_state to `emit`
    (event_bus.state) and SUPPRESSES repeats.

    The dedupe is the point: on_change fires on either axis, and several
    reducer states collapse to the same wire value (listening ->
    speech_detected is still "listening"; thinking -> transcribing is still
    "thinking"). Without this, a single utterance would emit a handful of
    identical {"type":"state"} frames to every connected HUD client.
    """
    last: list[str | None] = [None]

    def _on_change(state: "VoiceState") -> None:
        value = state.hud_state
        if value == last[0]:
            return
        last[0] = value
        emit(value)

    return _on_change
