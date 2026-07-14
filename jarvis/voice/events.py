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


# Playback completion is signaled by RealtimeVoiceEngine.speak_stream() returning
# (or its task being cancelled) — no separate event type needed for that; the
# orchestration loop already awaits/cancels that call directly.
VoiceEvent = Union[SpeechStarted, PartialTranscript, FinalTranscript, BargeIn, MicLevel]
