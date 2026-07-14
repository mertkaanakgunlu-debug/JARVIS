"""DuplexAudioIO: local full-duplex sounddevice I/O — satisfies the AudioIO
protocol using the microphone/speaker physically attached to the machine
running the Python backend (--voice CLI, or the API/wakeword/PTT loop).

Replaces the pre-Faz-3 voice.py's blocking sd.InputStream-per-utterance +
sd.play()+wait() pattern (neither of which could run concurrently or be
interrupted mid-flight) with:
  - one continuously-open, callback-mode InputStream feeding a thread-safe
    queue.Queue (never touch asyncio primitives from inside the PortAudio
    callback thread — it runs on PortAudio's own thread, not the event loop)
  - one callback-mode OutputStream per speaking turn (not per sentence, to
    avoid an abort-vs-reopen race), fed from an internal playback buffer,
    silence-filled on underrun rather than blocking
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
from collections import deque
from collections.abc import AsyncIterator

import numpy as np
import sounddevice as sd

from jarvis.config import Settings

logger = logging.getLogger(__name__)


class _PlaybackBuffer:
    """Thread-safe: push() is called from the asyncio/executor side, pull() from
    PortAudio's own OutputStream callback thread. Underruns are zero-padded —
    the callback must never block waiting for more data."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chunks: deque[np.ndarray] = deque()
        self._offset = 0

    def push(self, chunk: np.ndarray) -> None:
        if chunk.size == 0:
            return
        with self._lock:
            self._chunks.append(chunk)

    def pull(self, n: int) -> np.ndarray:
        out = np.zeros(n, dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < n and self._chunks:
                head = self._chunks[0]
                available = len(head) - self._offset
                take = min(available, n - filled)
                out[filled:filled + take] = head[self._offset:self._offset + take]
                filled += take
                self._offset += take
                if self._offset >= len(head):
                    self._chunks.popleft()
                    self._offset = 0
        return out

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._offset = 0

    def is_empty(self) -> bool:
        with self._lock:
            return not self._chunks


class DuplexAudioIO:
    """Satisfies jarvis.voice.io_base.AudioIO."""

    def __init__(self, settings: Settings, sample_rate: int = 16000, frame_samples: int = 512) -> None:
        self._settings = settings
        self._sample_rate = sample_rate
        self._frame_samples = frame_samples

        self._mic_queue: "queue.Queue[np.ndarray]" = queue.Queue()
        self._input_stream: sd.InputStream | None = None
        self._output_stream: sd.OutputStream | None = None
        self._output_rate: int | None = None
        self._playback_buffer = _PlaybackBuffer()

        self._last_input_rms = 0.0
        self.underrun_count = 0

    # ── Capture ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        def _input_callback(indata, frames, time_info, status) -> None:
            if status:
                logger.debug("[voice] input stream status: %s", status)
            mono = indata[:, 0].copy()
            self._mic_queue.put(mono)

        self._input_stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self._frame_samples,
            device=self._settings.audio_input_device or None,
            callback=_input_callback,
        )
        self._input_stream.start()

    async def stop(self) -> None:
        if self._input_stream is not None:
            self._input_stream.stop()
            self._input_stream.close()
            self._input_stream = None
        self._close_output_stream()

    async def raw_frames(self) -> AsyncIterator[np.ndarray]:
        loop = asyncio.get_running_loop()
        while self._input_stream is not None:
            frame = await loop.run_in_executor(None, self._mic_queue.get)
            self._last_input_rms = float(np.sqrt(np.mean(frame ** 2))) if frame.size else 0.0
            yield frame

    def mic_level(self) -> float:
        return self._last_input_rms

    # ── Playback ────────────────────────────────────────────────────────────

    def _open_output_stream(self, rate: int) -> None:
        self._close_output_stream()

        def _output_callback(outdata, frames, time_info, status) -> None:
            if status:
                logger.debug("[voice] output stream status: %s", status)
            chunk = self._playback_buffer.pull(frames)
            outdata[:, 0] = chunk

        self._output_stream = sd.OutputStream(
            samplerate=rate,
            channels=1,
            dtype="float32",
            device=self._settings.audio_output_device or None,
            callback=_output_callback,
        )
        self._output_rate = rate
        self._output_stream.start()

    def _close_output_stream(self) -> None:
        if self._output_stream is not None:
            try:
                self._output_stream.abort()
            except Exception:
                pass
            try:
                self._output_stream.close()
            except Exception:
                pass
            self._output_stream = None
            self._output_rate = None
        self._playback_buffer.clear()

    async def play_chunk(self, pcm: np.ndarray, sample_rate: int) -> None:
        if self._output_stream is None or self._output_rate != sample_rate:
            self._open_output_stream(sample_rate)
        self._playback_buffer.push(np.asarray(pcm, dtype=np.float32))

    async def wait_playback_drained(self, poll_interval: float = 0.02) -> None:
        while not self._playback_buffer.is_empty():
            await asyncio.sleep(poll_interval)
        # One extra beat so the last block already pulled by the callback
        # actually finishes rendering before the caller treats speech as "done".
        await asyncio.sleep(poll_interval)
        self._close_output_stream()

    def abort_playback(self) -> None:
        """Safe to call from any thread/context — barge-in must be immediate."""
        self._playback_buffer.clear()
        if self._output_stream is not None:
            try:
                self._output_stream.abort()
            except Exception:
                pass
