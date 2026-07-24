"""Typed events yielded by RealtimeVoiceEngine.events() / AudioIO implementations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union


@dataclass(frozen=True)
class SpeechStarted:
    """User started speaking (VAD crossed the speech-start gate)."""


@dataclass(frozen=True)
class PartialTranscript:
    """Optional live-caption preview of the in-progress utterance. Never used to
    drive a turn — only the FinalTranscript that follows end-of-turn is authoritative."""
    text: str


@dataclass(frozen=True)
class FinalTranscript:
    """End-of-turn (sustained silence) reached; this is the authoritative transcript."""
    text: str
    lang: str


@dataclass(frozen=True)
class BargeIn:
    """Sustained, high-confidence speech detected while JARVIS was speaking."""


@dataclass(frozen=True)
class MicLevel:
    """Real (not simulated) audio level, for HUD telemetry."""
    source: Literal["input", "output"]
    rms: float


@dataclass(frozen=True)
class TurnEnded:
    """VAD detected end-of-turn (sustained silence or the hard duration cap) --
    fires BEFORE STT runs, so a consumer can distinguish "still speaking" from
    "capture done, about to transcribe" (RealtimeVoiceEngine.events() otherwise
    only yields FinalTranscript AFTER the — potentially seconds-long — STT
    call completes, with nothing in between). Also fires for an EMPTY turn
    (captured_audio_duration_s == 0.0, no FinalTranscript follows) — Faz 3/8's
    known utterance-drop issue previously had zero observable signal at this
    exact point; this doesn't fix the drop, but makes it visible for the
    first time (Faz D, docs/eval's voice-observability work)."""
    reason: Literal["silence", "max_duration"]
    captured_audio_duration_s: float


# Playback completion is signaled by RealtimeVoiceEngine.speak_stream() returning
# (or its task being cancelled) — no separate event type needed for that; the
# orchestration loop already awaits/cancels that call directly.
VoiceEvent = Union[SpeechStarted, PartialTranscript, FinalTranscript, BargeIn, MicLevel, TurnEnded]
