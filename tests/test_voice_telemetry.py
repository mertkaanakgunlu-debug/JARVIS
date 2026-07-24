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


def test_input_overflow_count_starts_at_zero():
    assert _io().input_overflow_count == 0


def test_input_callback_increments_overflow_count_on_a_real_status():
    io = _io()
    indata = np.zeros((512, 1), dtype=np.float32)
    io._input_callback(indata, 512, None, "input overflow")
    assert io.input_overflow_count == 1


def test_input_callback_does_not_increment_on_a_clean_status():
    io = _io()
    indata = np.zeros((512, 1), dtype=np.float32)
    io._input_callback(indata, 512, None, None)
    assert io.input_overflow_count == 0


def test_input_callback_still_queues_the_frame_regardless_of_status():
    io = _io()
    indata = np.full((4, 1), 3.0, dtype=np.float32)
    io._input_callback(indata, 4, None, "overflow")
    assert io.queue_depth() == 1
    frame = io._mic_queue.get_nowait()
    assert np.all(frame == 3.0)


# ── queue_depth() ────────────────────────────────────────────────────────────

def test_queue_depth_reflects_unconsumed_frames():
    io = _io()
    assert io.queue_depth() == 0
    io._mic_queue.put(np.zeros(1))
    io._mic_queue.put(np.zeros(1))
    assert io.queue_depth() == 2
    io._mic_queue.get_nowait()
    assert io.queue_depth() == 1
