"""WavAudioIO: satisfies the AudioIO protocol (jarvis.voice.io_base) by
replaying a pre-recorded WAV file (or a raw float32 array, for synthetic
tests) instead of live microphone hardware — Faz F, the WAV-replay test
harness. Proves the seam the plan called out: RealtimeVoiceEngine takes
its AudioIO by constructor injection (jarvis/voice/engine.py's __init__),
so this is purely additive — no engine change needed to support it, the
same way DuplexAudioIO (local hardware) and RemoteWsAudioIO (/ws binary
frames) already prove two independent transports share identical
VAD/STT/TTS orchestration logic.

Re-chunking/resampling mirrors RemoteWsAudioIO.push_mic_frame() exactly
(same ENGINE_SAMPLE_RATE/ENGINE_FRAME_SAMPLES constants, same PCM16->float32
conversion, same scipy.signal.resample_poly path) rather than re-deriving
an independently-plausible-but-different implementation.
"""

from __future__ import annotations

import asyncio
import wave
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np

ENGINE_SAMPLE_RATE = 16000
ENGINE_FRAME_SAMPLES = 512   # must match jarvis.voice.vad.SileroVAD.frame_samples


def _read_wav_as_float32(path: "str | Path", target_rate: int = ENGINE_SAMPLE_RATE) -> np.ndarray:
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        raw = wf.readframes(wf.getnframes())

    if sampwidth != 2:
        raise ValueError(
            f"WavAudioIO only supports 16-bit PCM WAV files ({path}): "
            f"got sampwidth={sampwidth} bytes/sample"
        )

    pcm16 = np.frombuffer(raw, dtype="<i2")
    if n_channels > 1:
        pcm16 = pcm16.reshape(-1, n_channels)[:, 0].copy()  # first channel only
    floats = pcm16.astype(np.float32) / 32768.0

    if framerate != target_rate:
        from scipy.signal import resample_poly
        floats = resample_poly(floats, target_rate, framerate).astype(np.float32)
    return floats


class WavAudioIO:
    """Deterministic AudioIO replaying a fixed audio source -- for the
    WAV-replay test harness (Faz F), not production use.

    source: a WAV file path, or a raw mono float32 array already at 16kHz
    (synthetic tests that don't want a real file on disk).

    trailing_silence_s: appended after the source audio. Without this, a
    real utterance's audio ends mid-"speech" from VadTurnSegmenter's point
    of view (jarvis/voice/vad.py) -- it never sees the sustained silence
    that triggers a TurnEndedSignal, so replaying a WAV with no padding
    would hang forever waiting for a TurnEnded/FinalTranscript event that
    never comes. Default (1.6s) deliberately exceeds Settings.
    voice_silence_duration's own default (1.5s) so the silence gate fires
    even if a test overrides that setting slightly.
    """

    def __init__(
        self, source: "str | Path | np.ndarray", *, trailing_silence_s: float = 1.6,
    ) -> None:
        audio = source.astype(np.float32) if isinstance(source, np.ndarray) else _read_wav_as_float32(source)
        silence = np.zeros(int(trailing_silence_s * ENGINE_SAMPLE_RATE), dtype=np.float32)
        self._audio = np.concatenate([audio, silence])
        self._last_input_rms = 0.0
        self._active = True
        # For test assertions -- what was actually "played" (TTS output),
        # since there's no real speaker to send it to.
        self.played: list[tuple[np.ndarray, int]] = []

    async def start(self) -> None:
        self._active = True

    async def stop(self) -> None:
        self._active = False

    async def raw_frames(self) -> AsyncIterator[np.ndarray]:
        n = len(self._audio)
        for i in range(0, n, ENGINE_FRAME_SAMPLES):
            if not self._active:
                return
            frame = self._audio[i:i + ENGINE_FRAME_SAMPLES]
            if len(frame) < ENGINE_FRAME_SAMPLES:
                pad = np.zeros(ENGINE_FRAME_SAMPLES - len(frame), dtype=np.float32)
                frame = np.concatenate([frame, pad])
            self._last_input_rms = float(np.sqrt(np.mean(frame ** 2))) if frame.size else 0.0
            yield frame
            await asyncio.sleep(0)  # cooperate with the event loop like a real stream would

    def mic_level(self) -> float:
        return self._last_input_rms

    async def play_chunk(self, pcm: np.ndarray, sample_rate: int) -> None:
        self.played.append((np.asarray(pcm, dtype=np.float32), sample_rate))

    async def wait_playback_drained(self) -> None:
        pass  # nothing asynchronous to drain -- play_chunk() is a synchronous append

    def abort_playback(self) -> None:
        self.played.clear()
