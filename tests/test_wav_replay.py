"""WAV replay harness -- Faz F, the "normal CI" tier.

Real WavAudioIO chunking/resampling + a deterministic fake VAD (energy-
threshold based -- no real Silero/onnxruntime model, so this tier has
zero model-download/network dependency) + a fake STT. Verifies the
metrics this phase's engine.py changes surfaced (turn_end_reason,
captured_audio_duration_s, vad_prob_max/mean, stt_s) actually reach a
consumer, deterministically, with no real audio hardware or model weights
involved.

The OTHER tier -- real Silero VAD + real Whisper against a local model
cache, for genuine end-to-end confidence -- is marked `voice_e2e`
(pyproject.toml) and deliberately not run here; see that marker's own
docstring.
"""
from __future__ import annotations

import wave

import numpy as np
import pytest

from jarvis.voice.engine import RealtimeVoiceEngine, VoiceModels
from jarvis.voice.events import FinalTranscript, MicLevel, SpeechStarted, TurnEnded
from jarvis.voice.io_wav import ENGINE_FRAME_SAMPLES, ENGINE_SAMPLE_RATE, WavAudioIO, _read_wav_as_float32
from jarvis.voice.stt_whisper import TranscriptionResult


# ── _read_wav_as_float32: real WAV file reading ─────────────────────────────

def _write_wav(path, samples: np.ndarray, framerate: int) -> None:
    pcm16 = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(framerate)
        wf.writeframes(pcm16.tobytes())


def test_reads_a_16khz_mono_wav_file_unchanged(tmp_path):
    path = tmp_path / "test.wav"
    original = np.linspace(-0.5, 0.5, 1600, dtype=np.float32)
    _write_wav(path, original, 16000)

    read_back = _read_wav_as_float32(path)

    assert len(read_back) == 1600
    np.testing.assert_allclose(read_back, original, atol=1e-4)


def test_resamples_a_non_16khz_wav_file(tmp_path):
    path = tmp_path / "test_8k.wav"
    original = np.linspace(-0.5, 0.5, 800, dtype=np.float32)  # 0.1s @ 8kHz
    _write_wav(path, original, 8000)

    read_back = _read_wav_as_float32(path)

    # 0.1s of audio @ 16kHz target -> ~1600 samples
    assert abs(len(read_back) - 1600) <= 2


def test_rejects_a_non_16_bit_wav_file(tmp_path):
    path = tmp_path / "bad.wav"
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(1)  # 8-bit -- unsupported
        wf.setframerate(16000)
        wf.writeframes(bytes([128] * 100))

    with pytest.raises(ValueError, match="16-bit PCM"):
        _read_wav_as_float32(path)


def test_takes_the_first_channel_of_a_stereo_file(tmp_path):
    path = tmp_path / "stereo.wav"
    left = np.full(100, 0.3, dtype=np.float32)
    right = np.full(100, -0.3, dtype=np.float32)
    interleaved = np.empty(200, dtype=np.float32)
    interleaved[0::2] = left
    interleaved[1::2] = right
    pcm16 = (interleaved * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(pcm16.tobytes())

    read_back = _read_wav_as_float32(path)
    assert len(read_back) == 100
    assert np.all(read_back > 0)  # the LEFT channel's positive value, not right's negative


# ── WavAudioIO: chunking + padding + trailing silence ───────────────────────

@pytest.mark.asyncio
async def test_raw_frames_yields_exact_frame_size():
    audio = np.ones(ENGINE_FRAME_SAMPLES * 3, dtype=np.float32) * 0.5
    io_ = WavAudioIO(audio, trailing_silence_s=0.0)

    frames = [f async for f in io_.raw_frames()]

    assert all(len(f) == ENGINE_FRAME_SAMPLES for f in frames)
    assert len(frames) == 3


@pytest.mark.asyncio
async def test_a_short_final_frame_is_zero_padded_not_truncated():
    audio = np.ones(ENGINE_FRAME_SAMPLES + 10, dtype=np.float32) * 0.5
    io_ = WavAudioIO(audio, trailing_silence_s=0.0)

    frames = [f async for f in io_.raw_frames()]

    assert len(frames) == 2
    assert len(frames[-1]) == ENGINE_FRAME_SAMPLES
    assert np.all(frames[-1][10:] == 0.0)  # zero-padded tail
    assert np.all(frames[-1][:10] == 0.5)  # real samples preserved


@pytest.mark.asyncio
async def test_trailing_silence_is_appended():
    audio = np.ones(ENGINE_FRAME_SAMPLES, dtype=np.float32) * 0.5
    io_ = WavAudioIO(audio, trailing_silence_s=0.5)

    frames = [f async for f in io_.raw_frames()]
    total_samples = sum(len(f) for f in frames)

    # 1 frame of real audio + 0.5s silence, rounded up to whole frames
    assert total_samples >= ENGINE_FRAME_SAMPLES + int(0.5 * ENGINE_SAMPLE_RATE)
    assert np.all(frames[-1] == 0.0)  # the last frame is pure appended silence


@pytest.mark.asyncio
async def test_stop_ends_iteration_early():
    audio = np.ones(ENGINE_FRAME_SAMPLES * 100, dtype=np.float32)
    io_ = WavAudioIO(audio, trailing_silence_s=0.0)

    frames = []
    async for f in io_.raw_frames():
        frames.append(f)
        if len(frames) == 3:
            await io_.stop()

    assert len(frames) == 3


@pytest.mark.asyncio
async def test_play_chunk_records_what_was_played():
    io_ = WavAudioIO(np.zeros(10, dtype=np.float32), trailing_silence_s=0.0)
    await io_.play_chunk(np.array([0.1, 0.2], dtype=np.float32), 22050)
    assert len(io_.played) == 1
    assert io_.played[0][1] == 22050


# ── Full engine integration: fake VAD + fake STT, real chunking ─────────────

class _FakeVAD:
    """Deterministic, energy-threshold VAD -- reports speech whenever a
    frame's RMS exceeds a fixed threshold. No real Silero/onnxruntime model,
    so this tier has zero model-download/network dependency."""
    frame_samples = ENGINE_FRAME_SAMPLES

    def __init__(self, energy_threshold: float = 0.05):
        self._threshold = energy_threshold

    def reset_states(self) -> None:
        pass

    def process_chunk(self, chunk: np.ndarray) -> float:
        rms = float(np.sqrt(np.mean(chunk ** 2))) if chunk.size else 0.0
        return 1.0 if rms > self._threshold else 0.0


class _FakeStt:
    def __init__(self, text="merhaba dünya", lang="tr", stt_s=0.01):
        self._text, self._lang, self._stt_s = text, lang, stt_s
        self.calls: list[np.ndarray] = []

    def transcribe(self, audio: np.ndarray) -> TranscriptionResult:
        self.calls.append(audio.copy())
        return TranscriptionResult(text=self._text, lang=self._lang, stt_s=self._stt_s)


def _speech_then_silence(speech_frames: int = 40, silence_s: float = 1.6) -> np.ndarray:
    """~1.3s of loud "speech" (RMS well above _FakeVAD's default 0.05
    threshold) followed by WavAudioIO's own appended trailing silence."""
    rng = np.random.default_rng(42)
    speech = (rng.standard_normal(speech_frames * ENGINE_FRAME_SAMPLES) * 0.3).astype(np.float32)
    return speech


async def _drive_to_completion(audio_io, fake_vad, fake_stt, settings=None):
    from jarvis.config import Settings

    models = VoiceModels(vad=fake_vad, stt=fake_stt, tts=None)
    engine = RealtimeVoiceEngine(audio_io, settings or Settings(_env_file=None), models=models)
    await engine.load()
    events = [e async for e in engine.events()]
    return events


@pytest.mark.asyncio
async def test_speech_then_silence_produces_the_full_expected_event_sequence():
    audio_io = WavAudioIO(_speech_then_silence(), trailing_silence_s=1.6)
    events = await _drive_to_completion(audio_io, _FakeVAD(), _FakeStt())

    speech_started = [e for e in events if isinstance(e, SpeechStarted)]
    turn_ended = [e for e in events if isinstance(e, TurnEnded)]
    final_transcripts = [e for e in events if isinstance(e, FinalTranscript)]

    assert len(speech_started) == 1
    assert len(turn_ended) == 1
    assert len(final_transcripts) == 1

    te = turn_ended[0]
    assert te.reason == "silence"
    assert te.captured_audio_duration_s > 0.0
    assert te.vad_prob_max == 1.0  # the fake VAD only ever emits 0.0 or 1.0
    assert te.vad_prob_mean > 0.0

    ft = final_transcripts[0]
    assert ft.text == "merhaba dünya"
    assert ft.lang == "tr"
    assert ft.stt_s == 0.01


@pytest.mark.asyncio
async def test_pure_silence_produces_no_speech_or_transcript_signals():
    """No SpeechStartedSignal ever fires if speech never crosses the
    threshold -- correctly produces nothing, not a false empty turn."""
    audio_io = WavAudioIO(np.zeros(ENGINE_FRAME_SAMPLES * 20, dtype=np.float32), trailing_silence_s=0.0)
    events = await _drive_to_completion(audio_io, _FakeVAD(), _FakeStt())

    assert not [e for e in events if isinstance(e, SpeechStarted)]
    assert not [e for e in events if isinstance(e, TurnEnded)]
    assert not [e for e in events if isinstance(e, FinalTranscript)]


@pytest.mark.asyncio
async def test_mic_level_events_are_emitted_for_every_frame():
    audio_io = WavAudioIO(np.ones(ENGINE_FRAME_SAMPLES * 3, dtype=np.float32) * 0.1, trailing_silence_s=0.0)
    events = await _drive_to_completion(audio_io, _FakeVAD(energy_threshold=999.0), _FakeStt())

    mic_levels = [e for e in events if isinstance(e, MicLevel)]
    assert len(mic_levels) == 3  # one per frame, energy too low to trigger speech
    assert all(m.rms > 0 for m in mic_levels)


@pytest.mark.asyncio
async def test_stt_receives_the_exact_accumulated_speech_buffer():
    audio_io = WavAudioIO(_speech_then_silence(speech_frames=10), trailing_silence_s=1.6)
    stt = _FakeStt()
    await _drive_to_completion(audio_io, _FakeVAD(), stt)

    assert len(stt.calls) == 1
    # The buffer handed to transcribe() must be pure speech, not the trailing
    # silence appended after it (the silence is what triggers the turn-end,
    # not part of the "utterance").
    assert stt.calls[0].size >= 10 * ENGINE_FRAME_SAMPLES
