"""jarvis/voice/io_duplex.py -- Faz D (voice observability) telemetry.

Before this phase, underrun_count was declared but never incremented
anywhere (a dead counter), input stream `status` only ever reached
logger.debug (no counter at all), and queue depth was never exposed
despite queue.Queue tracking it for free. _input_callback/_output_callback
were refactored from closures defined inline in start()/_open_output_stream()
into bound methods specifically so they're testable here without a real
PortAudio stream.
"""
from __future__ import annotations

import numpy as np

from jarvis.config import Settings
from jarvis.voice.io_duplex import DuplexAudioIO, _PlaybackBuffer


# ── _PlaybackBuffer.pull(): underrun detection ──────────────────────────────

def test_pull_reports_no_underrun_when_enough_data_buffered():
    buf = _PlaybackBuffer()
    buf.push(np.ones(100, dtype=np.float32))
    out, underran = buf.pull(50)
    assert underran is False
    assert len(out) == 50
    assert np.all(out == 1.0)


def test_pull_reports_underrun_when_buffer_runs_short():
    buf = _PlaybackBuffer()
    buf.push(np.ones(10, dtype=np.float32))
    out, underran = buf.pull(50)  # only 10 available, asked for 50
    assert underran is True
    assert len(out) == 50
    assert np.all(out[:10] == 1.0)
    assert np.all(out[10:] == 0.0)  # zero-padded tail


def test_pull_reports_underrun_on_a_totally_empty_buffer():
    buf = _PlaybackBuffer()
    out, underran = buf.pull(20)
    assert underran is True
    assert np.all(out == 0.0)


def test_pull_consumes_across_multiple_chunks_without_underrun():
    buf = _PlaybackBuffer()
    buf.push(np.full(5, 1.0, dtype=np.float32))
    buf.push(np.full(5, 2.0, dtype=np.float32))
    out, underran = buf.pull(10)
    assert underran is False
    assert np.all(out[:5] == 1.0) and np.all(out[5:] == 2.0)


# ── DuplexAudioIO: the counters actually increment now ──────────────────────

def _io() -> DuplexAudioIO:
    return DuplexAudioIO(Settings(_env_file=None))


def test_underrun_count_starts_at_zero():
    assert _io().underrun_count == 0


def test_output_callback_increments_underrun_count_on_a_real_shortfall():
    io = _io()
    io._playback_buffer.push(np.zeros(5, dtype=np.float32))  # far less than requested
    outdata = np.zeros((100, 1), dtype=np.float32)
    io._output_callback(outdata, 100, None, None)
    assert io.underrun_count == 1


def test_output_callback_does_not_increment_when_fully_supplied():
    io = _io()
    io._playback_buffer.push(np.zeros(100, dtype=np.float32))
    outdata = np.zeros((50, 1), dtype=np.float32)
    io._output_callback(outdata, 50, None, None)
    assert io.underrun_count == 0


class _Flags:
    """Stand-in for sounddevice.CallbackFlags: truthy whenever PortAudio
    reported anything, with a separate input_overflow attribute for the
    specific condition that means microphone audio was actually dropped."""

    def __init__(self, *, input_overflow: bool = False, other: bool = False):
        self.input_overflow = input_overflow
        self._truthy = input_overflow or other

    def __bool__(self) -> bool:
        return self._truthy


def test_input_status_and_overflow_counts_start_at_zero():
    io = _io()
    assert io.input_status_count == 0
    assert io.input_overflow_count == 0
    assert io.capture_callback_count == 0
    assert io.capture_consumed_frame_count == 0
    assert io.capture_queue_high_watermark == 0


def test_a_real_input_overflow_increments_both_counters():
    io = _io()
    indata = np.zeros((512, 1), dtype=np.float32)
    io._input_callback(indata, 512, None, _Flags(input_overflow=True))
    assert io.input_status_count == 1
    assert io.input_overflow_count == 1
    assert io.capture_input_status_count == 1
    assert io.capture_input_overflow_count == 1


def test_a_non_overflow_status_counts_as_status_but_not_as_overflow():
    """The review finding: CallbackFlags is truthy for ANY reported
    condition (e.g. a priming/output flag), so counting every truthy status
    as an input overflow overstated the specific failure the name promises."""
    io = _io()
    indata = np.zeros((512, 1), dtype=np.float32)
    io._input_callback(indata, 512, None, _Flags(other=True))
    assert io.input_status_count == 1
    assert io.input_overflow_count == 0


def test_input_callback_does_not_increment_on_a_clean_status():
    io = _io()
    indata = np.zeros((512, 1), dtype=np.float32)
    io._input_callback(indata, 512, None, None)
    assert io.input_status_count == 0
    assert io.input_overflow_count == 0


def test_input_callback_still_queues_the_frame_regardless_of_status():
    io = _io()
    indata = np.full((4, 1), 3.0, dtype=np.float32)
    io._input_callback(indata, 4, None, _Flags(input_overflow=True))
    assert io.queue_depth() == 1
    frame = io._mic_queue.get_nowait()
    assert np.all(frame == 3.0)
    assert io.capture_callback_count == 1
    assert io.capture_sample_count == 4
    assert io.capture_queue_high_watermark == 1
    assert io.capture_frame_size_mismatch_count == 1


# ── queue_depth() ────────────────────────────────────────────────────────────

def test_queue_depth_reflects_unconsumed_frames():
    io = _io()
    assert io.queue_depth() == 0
    io._mic_queue.put(np.zeros(1))
    io._mic_queue.put(np.zeros(1))
    assert io.queue_depth() == 2
    io._mic_queue.get_nowait()
    assert io.queue_depth() == 1


def test_raw_frame_consumption_advances_the_capture_stage_ladder():
    import asyncio

    io = _io()
    io._input_stream = object()
    io._input_callback(np.zeros((512, 1), dtype=np.float32), 512, None, None)

    async def _consume_one():
        frame = await anext(io.raw_frames())
        io._input_stream = None
        return frame

    frame = asyncio.run(_consume_one())
    assert frame.size == 512
    assert io.capture_callback_count == 1
    assert io.capture_consumed_frame_count == 1
    assert io.capture_consumed_sample_count == 512
    assert io.capture_frame_size_mismatch_count == 0
