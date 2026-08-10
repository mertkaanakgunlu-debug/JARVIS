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

    def pull(self, n: int) -> tuple[np.ndarray, bool]:
        """Returns (samples, underran) -- underran is True when fewer than n
        samples were actually available and the tail was zero-padded (Faz D:
        this was previously silently discarded -- DuplexAudioIO.underrun_count
        was declared but never incremented anywhere)."""
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
        return out, filled < n

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
        # Faz D (voice observability): input stream `status` previously only
        # ever reached logger.debug -- no counter existed for a user/HUD to
        # ever learn a real capture problem occurred at all.
        #
        # Two counters, not one (review finding, 2026-07-25): PortAudio's
        # CallbackFlags is truthy for ANY condition it reports, so counting
        # every truthy `status` as an overflow overstated the specific
        # failure the name promised. input_status_count is the honest
        # "PortAudio flagged something" tally; input_overflow_count now only
        # counts a real input_overflow, which is the one that actually means
        # dropped microphone audio.
        self.input_status_count = 0
        self.input_overflow_count = 0
        self._reset_capture_metrics()

    def _reset_capture_metrics(self) -> None:
        """Reset the stage counters for one local capture activation.

        Cumulative status/overflow counters above remain available for the
        process lifetime.  These counters answer the narrower localization
        question: how far did audio travel during the current/last activation?
        """
        self.capture_callback_count = 0
        self.capture_sample_count = 0
        self.capture_consumed_frame_count = 0
        self.capture_consumed_sample_count = 0
        self.capture_frame_size_mismatch_count = 0
        self.capture_input_status_count = 0
        self.capture_input_overflow_count = 0
        self.capture_queue_initial_depth = self._mic_queue.qsize()
        self.capture_queue_high_watermark = self.capture_queue_initial_depth

    # ── Capture ─────────────────────────────────────────────────────────────

    def _input_callback(self, indata, frames, time_info, status) -> None:
        # Faz D: a bound method (not a closure defined inline in start()) so
        # it's directly unit-testable without a real PortAudio stream -- see
        # tests/test_voice_telemetry.py.
        if status:
            logger.debug("[voice] input stream status: %s", status)
            self.input_status_count += 1
            self.capture_input_status_count += 1
            # getattr, not status.input_overflow: sounddevice's CallbackFlags
            # is the normal case, but the callback is also driven directly by
            # tests (and, in principle, by any other PortAudio binding) with
            # a plainer status object that has no such attribute.
            if getattr(status, "input_overflow", False):
                self.input_overflow_count += 1
                self.capture_input_overflow_count += 1
        mono = indata[:, 0].copy()
        self.capture_callback_count += 1
        self.capture_sample_count += int(mono.size)
        if frames != self._frame_samples or mono.size != self._frame_samples:
            self.capture_frame_size_mismatch_count += 1
        self._mic_queue.put(mono)
        self.capture_queue_high_watermark = max(
            self.capture_queue_high_watermark, self._mic_queue.qsize(),
        )

    async def start(self) -> None:
        self._reset_capture_metrics()
        self._input_stream = sd.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
            blocksize=self._frame_samples,
            device=self._settings.audio_input_device or None,
            callback=self._input_callback,
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
            self.capture_consumed_frame_count += 1
            self.capture_consumed_sample_count += int(frame.size)
            self._last_input_rms = float(np.sqrt(np.mean(frame ** 2))) if frame.size else 0.0
            yield frame

    def mic_level(self) -> float:
        return self._last_input_rms

    def queue_depth(self) -> int:
        """Frames buffered but not yet consumed by raw_frames() -- Faz D
        (voice observability): previously computable but never exposed,
        the one telemetry value a "is capture keeping up?" diagnostic needs
        that queue.Queue itself already tracks for free."""
        return self._mic_queue.qsize()

    # ── Playback ────────────────────────────────────────────────────────────

    def _output_callback(self, outdata, frames, time_info, status) -> None:
        if status:
            logger.debug("[voice] output stream status: %s", status)
        chunk, underran = self._playback_buffer.pull(frames)
        if underran:
            self.underrun_count += 1
        outdata[:, 0] = chunk

    def _open_output_stream(self, rate: int) -> None:
        self._close_output_stream()
        self._output_stream = sd.OutputStream(
            samplerate=rate,
            channels=1,
            dtype="float32",
            device=self._settings.audio_output_device or None,
            callback=self._output_callback,
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
