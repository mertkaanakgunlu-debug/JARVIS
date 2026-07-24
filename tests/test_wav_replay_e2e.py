"""Faz F -- the voice_e2e tier: real Silero VAD + real Whisper against a
local model cache, replaying real Piper-synthesized Turkish audio via
WavAudioIO. NOT run automatically (pyproject.toml's addopts excludes
`voice_e2e` by default -- see that marker's own docstring) -- this is the
genuinely-end-to-end tier that test_wav_replay.py's fake-VAD/fake-STT,
network-free tier deliberately is not. The two tiers together give real
separation power: if THIS test passes but a live microphone still drops
utterances, the problem is proven to be downstream of VAD/STT (device/
PortAudio/mic-gating), not the model pipeline itself.

Fixture audio (tests/audio/*.wav) is Piper-synthesized, not a personal
recording -- safe to commit, deterministic, and directly ties back to the
real live-caught bugs this project fixed (jarvis/voice/session.py's
is_affirmative trailing-period handling, jarvis/voice/text.py's exit-phrase
detection): these tests prove the REAL Whisper transcript of "Evet."/
"Hayır."/"Güle güle." is still classified correctly by those functions,
not just that a hand-typed string is.

Run explicitly: pytest -m voice_e2e tests/test_wav_replay_e2e.py
(first run downloads Whisper's model weights if not already cached --
this is exactly why this tier is excluded from the default/CI run).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config import Settings
from jarvis.voice.engine import RealtimeVoiceEngine, VoiceModels
from jarvis.voice.events import FinalTranscript, TurnEnded
from jarvis.voice.io_wav import WavAudioIO
from jarvis.voice.session import is_affirmative
from jarvis.voice.stt_whisper import WhisperSTT
from jarvis.voice.text import is_exit_phrase
from jarvis.voice.vad import SileroVAD, ensure_silero_vad_model

pytestmark = pytest.mark.voice_e2e

_AUDIO_DIR = Path(__file__).parent / "audio"


@pytest.fixture(scope="module")
def real_models():
    """Loaded ONCE per test module -- Whisper load alone is several seconds;
    no point paying it per test in this file."""
    settings = Settings(_env_file=None, whisper_language="tr")
    vad_path = ensure_silero_vad_model()
    vad = SileroVAD(vad_path)
    stt = WhisperSTT(settings)
    stt.load()
    return VoiceModels(vad=vad, stt=stt, tts=None), settings


async def _replay(path: Path, models: VoiceModels, settings: Settings) -> list:
    audio_io = WavAudioIO(path)
    engine = RealtimeVoiceEngine(audio_io, settings, models=models)
    await engine.load()
    return [e async for e in engine.events()]


@pytest.mark.asyncio
async def test_evet_onayliyorum_is_transcribed_and_recognized_as_affirmative(real_models):
    """A full, natural phrase, not the bare single word -- see the xfail
    test below for why the bare word is a documented separate finding."""
    models, settings = real_models
    events = await _replay(_AUDIO_DIR / "evet_onayliyorum.wav", models, settings)

    transcripts = [e for e in events if isinstance(e, FinalTranscript)]
    assert transcripts, "expected at least one FinalTranscript from real Whisper"
    text = transcripts[0].text
    assert is_affirmative(text), f"real Whisper transcript {text!r} not recognized as affirmative"


@pytest.mark.asyncio
@pytest.mark.xfail(
    reason="Live finding (Faz F, 2026-07-24): a BARE single-word 'Evet.' "
           "Piper-synthesized utterance does not reliably round-trip through "
           "real Whisper -- observed transcripts 'Devleti.'/'Rövlet.' across "
           "separate runs (real beam search, not literally random, but "
           "sensitive to encoder-buffer edge effects on a very short, "
           "context-free utterance). The identical phrase WITH natural "
           "sentence context ('Evet, onaylıyorum.', tested above) is "
           "reliable. Kept as an honest xfail rather than deleted so a "
           "future STT/VAD change that fixes this is noticed (XPASS), not "
           "silently re-broken later.",
    strict=False,
)
async def test_bare_evet_word_is_a_known_unreliable_short_utterance(real_models):
    models, settings = real_models
    events = await _replay(_AUDIO_DIR / "evet.wav", models, settings)

    transcripts = [e for e in events if isinstance(e, FinalTranscript)]
    assert transcripts
    assert is_affirmative(transcripts[0].text)


@pytest.mark.asyncio
async def test_hayir_is_transcribed_and_not_recognized_as_affirmative(real_models):
    models, settings = real_models
    events = await _replay(_AUDIO_DIR / "hayir.wav", models, settings)

    transcripts = [e for e in events if isinstance(e, FinalTranscript)]
    assert transcripts
    assert not is_affirmative(transcripts[0].text)


@pytest.mark.asyncio
async def test_gule_gule_is_transcribed_and_recognized_as_exit_phrase(real_models):
    models, settings = real_models
    events = await _replay(_AUDIO_DIR / "gule_gule.wav", models, settings)

    transcripts = [e for e in events if isinstance(e, FinalTranscript)]
    assert transcripts
    text = transcripts[0].text
    assert is_exit_phrase(text), f"real Whisper transcript {text!r} not recognized as exit phrase"


@pytest.mark.asyncio
async def test_turn_ended_reports_real_measured_metrics(real_models):
    """The exact 8-field measurement set docs/eval/owner_extended_corpus.md's
    plan named (speech_started, turn_ended, turn_end_reason,
    captured_audio_duration, transcript, language, STT duration, VAD max/
    mean probability) -- all real, not asserted against a fake."""
    models, settings = real_models
    events = await _replay(_AUDIO_DIR / "evet.wav", models, settings)

    turn_ended = [e for e in events if isinstance(e, TurnEnded)]
    transcripts = [e for e in events if isinstance(e, FinalTranscript)]
    assert turn_ended and transcripts

    te = turn_ended[0]
    assert te.reason == "silence"
    assert te.captured_audio_duration_s > 0.0
    assert 0.0 < te.vad_prob_mean <= 1.0
    assert te.vad_prob_max >= te.vad_prob_mean

    ft = transcripts[0]
    assert ft.lang == "tr"
    assert ft.stt_s > 0.0
