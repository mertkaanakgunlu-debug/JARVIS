"""AudioIO: the transport boundary RealtimeVoiceEngine is built against.

Deliberately thin — raw audio frames in, already-synthesized PCM chunks out.
All VAD/STT/TTS/barge-in *logic* lives once in RealtimeVoiceEngine, not here,
so DuplexAudioIO (local sounddevice) and RemoteWsAudioIO (binary /ws frames)
never duplicate that logic — they only differ in where the bytes physically
come from/go to.

A typing.Protocol (structural typing) rather than an ABC: a concrete class
satisfies this by having the right methods, no explicit inheritance required.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

import numpy as np


@runtime_checkable
class AudioIO(Protocol):
    async def start(self) -> None:
        """Begin capture (and anything else needed before frames/playback work)."""
        ...

    async def stop(self) -> None:
        """Tear down capture/playback resources."""
        ...

    def raw_frames(self) -> AsyncIterator[np.ndarray]:
        """Yields fixed-size mono float32 frames at the engine's working sample
        rate (16kHz), as they arrive."""
        ...

    async def play_chunk(self, pcm: np.ndarray, sample_rate: int) -> None:
        """Enqueue one already-synthesized PCM chunk for playback/transport.
        Non-blocking: returns once the chunk is handed off, not once it's audible."""
        ...

    async def wait_playback_drained(self) -> None:
        """Block until everything handed to play_chunk() has finished playing/
        sending — used at natural end-of-turn to know when to close out playback."""
        ...

    def abort_playback(self) -> None:
        """Immediately silence playback (barge-in). Safe to call from any thread."""
        ...

    def mic_level(self) -> float:
        """Most recent input RMS — real (not simulated) telemetry for the HUD."""
        ...
