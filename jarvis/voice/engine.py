"""RealtimeVoiceEngine: the transport-blind orchestrator. Composes VAD (end-of-turn
+ barge-in detection), STT (one-shot Whisper at end-of-turn), and TTS (Piper/cloud
fallback) against an injected AudioIO backend (DuplexAudioIO for local hardware,
RemoteWsAudioIO for a /ws-connected client) — identical logic either way.

Barge-in responsibility split (see MEMORY.md/ROADMAP.md for the reasoning):
  - This class stops audio immediately (abort_playback()) and flips an internal
    flag the moment sustained high-confidence speech is detected during playback,
    then yields a BargeIn event.
  - It is the ORCHESTRATION LOOP (cli.py/voice_api.py), not this class, that must
    cancel the actual agent.chat_stream() task feeding speak_stream()'s text —
    this engine has no visibility into that task. speak_stream() itself only
    cooperatively stops *synthesizing further sentences* once flagged; it cannot
    cancel the caller's LLM generation.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass

import numpy as np

from jarvis.config import Settings
from jarvis.voice.events import (
    BargeIn,
    FinalTranscript,
    MicLevel,
    SpeechStarted,
    TurnEnded,
    VoiceEvent,
)
from jarvis.voice.io_base import AudioIO
from jarvis.voice.stt_whisper import WhisperSTT
from jarvis.voice.text import sanitize_for_tts
from jarvis.voice.tts_piper import TtsRouter
from jarvis.voice.vad import SileroVAD, ensure_silero_vad_model
from jarvis.voice.vad import (
    SpeechStartedSignal,
    SustainedGate,
    TurnEndedSignal,
    VadTurnSegmenter,
)

logger = logging.getLogger(__name__)


@dataclass
class VoiceModels:
    """The expensive-to-load, safely-shareable pieces (VAD onnx session, Whisper
    model, Piper voices) — loaded ONCE and passed to every RealtimeVoiceEngine
    instance (local loop, and one per remote-audio /ws session), instead of each
    instance reloading Whisper/Piper/VAD from scratch. Safe to share because
    jarvis.voice.session_manager enforces that at most one engine instance is
    ever actively processing audio at a time (local XOR one remote client) —
    RealtimeVoiceEngine.events() already resets SileroVAD's recurrent state at
    the start of every session, which is exactly what's needed for a shared,
    sequentially-reused VAD instance to behave correctly."""
    vad: SileroVAD
    stt: WhisperSTT
    tts: TtsRouter

    @classmethod
    async def load(cls, settings: Settings) -> "VoiceModels":
        loop = asyncio.get_running_loop()
        model_path = await loop.run_in_executor(None, ensure_silero_vad_model)
        vad = await loop.run_in_executor(None, SileroVAD, model_path)
        stt = WhisperSTT(settings)
        await loop.run_in_executor(None, stt.load)
        return cls(vad=vad, stt=stt, tts=TtsRouter(settings))


_shared_models: VoiceModels | None = None
_shared_models_lock = asyncio.Lock()


async def get_shared_voice_models(settings: Settings) -> VoiceModels:
    """Lazy singleton, safe regardless of which caller runs first: the local
    --voice/wakeword loop (jarvis/voice_api.py, at server startup) and the
    first remote-audio /ws session (jarvis/api.py) both call this — whichever
    actually runs first loads the models once; everyone else reuses the same
    instance. Safe to share for the same reason VoiceModels itself is (see its
    docstring): at most one engine instance is ever actively processing audio
    at a time. Only meaningful within one process (e.g. one `--api` server) —
    a separately-invoked `--voice` CLI process has its own memory space and
    loads its own models regardless, which is correct (no shared state to race)."""
    global _shared_models
    async with _shared_models_lock:
        if _shared_models is None:
            _shared_models = await VoiceModels.load(settings)
        return _shared_models


class RealtimeVoiceEngine:
    def __init__(self, audio_io: AudioIO, settings: Settings, models: VoiceModels | None = None) -> None:
        self._audio_io = audio_io
        self._settings = settings
        self._models = models

        self._segmenter: VadTurnSegmenter | None = None
        self._barge_in_gate: SustainedGate | None = None

        self._speaking = False

        # Faz D telemetry, retained rather than only yielded. TurnEnded/
        # FinalTranscript carry these numbers to whoever is consuming
        # events() at that instant and are then gone -- so a later "how did
        # the last turn actually go?" question (jarvis/voice/diagnostics.py,
        # the /voice-status surfaces) had nothing to read. Last turn only:
        # this is a diagnostic, not a history.
        self.last_turn_metrics: dict = {}
        # Per-events() activation stage ladder.  Unlike last_turn_metrics this
        # remains meaningful when no turn reaches SpeechStarted/TurnEnded.
        self.capture_metrics: dict = {}

    async def load(self) -> None:
        """Loads shared models if none were injected via the constructor (the
        local --voice/wakeword loop's normal path); a remote-audio session
        instead passes in already-loaded VoiceModels and this becomes a cheap
        no-op for the model-loading part. Either way, builds this instance's
        own segmenter/barge-in gate (cheap, kept per-instance for clarity even
        though sharing would also be safe — see VoiceModels' docstring)."""
        if self._models is None:
            self._models = await VoiceModels.load(self._settings)

        frame_duration_s = self._models.vad.frame_samples / 16000
        self._segmenter = VadTurnSegmenter(
            speech_threshold=self._settings.vad_speech_threshold,
            silence_duration_s=self._settings.voice_silence_duration,
            frame_duration_s=frame_duration_s,
        )
        barge_in_min_frames = max(1, round(self._settings.vad_barge_in_duration_s / frame_duration_s))
        self._barge_in_gate = SustainedGate(
            threshold=self._settings.vad_barge_in_threshold,
            min_frames=barge_in_min_frames,
            above=True,
        )

    async def start(self) -> None:
        await self._audio_io.start()

    async def stop(self) -> None:
        await self._audio_io.stop()

    def abort_playback(self) -> None:
        self._speaking = False
        self._audio_io.abort_playback()

    async def events(self) -> AsyncIterator[VoiceEvent]:
        """Runs for the lifetime of the session — the orchestration loop should
        consume this as its own continuously-running task (see module docstring
        on why: barge-in detection must keep running concurrently with speak_stream())."""
        assert self._models is not None and self._segmenter is not None, "call load() first"
        vad = self._models.vad

        vad.reset_states()
        self._segmenter.reset()
        self.capture_metrics = {
            "vad_frame_count": 0,
            "frame_contract_mismatch_count": 0,
            "speech_started_count": 0,
            "turn_ended_count": 0,
            "stt_attempt_count": 0,
            "stt_nonempty_count": 0,
            "recent_frame_count": 0,
            "recent_input_rms_max": 0.0,
            "recent_vad_prob_max": 0.0,
            "recent_vad_prob_mean": 0.0,
        }
        # Five seconds of frame-level evidence is long enough to cover a
        # natural command while keeping continuous-listen diagnostics recent.
        recent_frame_limit = max(1, round(5.0 * 16000 / vad.frame_samples))
        recent_rms: deque[float] = deque(maxlen=recent_frame_limit)
        recent_vad: deque[float] = deque(maxlen=recent_frame_limit)
        buffer: list[np.ndarray] = []
        # Rolling tail of the most recent frames, sized to the barge-in gate's
        # own confirmation window. Without this, the frames that built up
        # confidence for a barge-in decision (up to vad_barge_in_duration_s,
        # 0.4s by default) would be silently dropped — the user's interrupting
        # utterance would have its first fraction of a second missing from
        # whatever eventually gets transcribed. Seeded into `buffer` the moment
        # barge-in fires, then the segmenter picks up normally from there.
        barge_in_tail: deque[np.ndarray] = deque(maxlen=self._barge_in_gate.min_frames)

        # Faz F (WAV replay harness): per-frame VAD probability, parallel to
        # `buffer`, aggregated into TurnEnded's vad_prob_max/mean. Reset
        # alongside buffer at every point buffer itself is reset/reseeded.
        vad_probs: list[float] = []

        async for frame in self._audio_io.raw_frames():
            if frame.ndim != 1 or frame.size != vad.frame_samples:
                self.capture_metrics["frame_contract_mismatch_count"] += 1
            prob = vad.process_chunk(frame)
            rms = self._audio_io.mic_level()
            self.capture_metrics["vad_frame_count"] += 1
            recent_rms.append(rms)
            recent_vad.append(prob)
            self.capture_metrics.update({
                "recent_frame_count": len(recent_vad),
                "recent_input_rms_max": max(recent_rms),
                "recent_vad_prob_max": max(recent_vad),
                "recent_vad_prob_mean": sum(recent_vad) / len(recent_vad),
            })
            yield MicLevel(source="input", rms=rms)

            if self._speaking:
                barge_in_tail.append(frame)
                if self._barge_in_gate.push(prob):
                    self._barge_in_gate.reset()
                    self.abort_playback()
                    buffer = list(barge_in_tail)
                    # The tail's own per-frame probabilities weren't tracked
                    # in parallel (barge_in_tail only ever held raw frames) --
                    # starting fresh here means a barge-in utterance's
                    # aggregate honestly reflects only the frames captured
                    # AFTER the barge-in fired, not a fabricated full history.
                    vad_probs = []
                    barge_in_tail.clear()
                    self._segmenter.force_speaking()
                    yield BargeIn()
                continue

            signal = self._segmenter.push(prob)
            if isinstance(signal, SpeechStartedSignal):
                self.capture_metrics["speech_started_count"] += 1
                buffer = [frame]
                vad_probs = [prob]
                yield SpeechStarted()
            elif isinstance(signal, TurnEndedSignal):
                self.capture_metrics["turn_ended_count"] += 1
                audio = np.concatenate(buffer) if buffer else np.array([], dtype=np.float32)
                buffer = []
                probs_this_turn, vad_probs = vad_probs, []
                # Faz D (voice observability): yielded BEFORE the (potentially
                # seconds-long) STT call, and even for an empty turn -- see
                # TurnEnded's own docstring for why this ordering/inclusion
                # matters (a consumer needs a "capture just ended" signal
                # distinct from "STT finished", and an empty turn previously
                # had no observable signal at all here).
                turn_ended = TurnEnded(
                    reason=signal.reason,
                    captured_audio_duration_s=audio.size / 16000,
                    vad_prob_max=max(probs_this_turn) if probs_this_turn else 0.0,
                    vad_prob_mean=(sum(probs_this_turn) / len(probs_this_turn)) if probs_this_turn else 0.0,
                )
                # Retained for /voice-status BEFORE being yielded, so the
                # numbers are readable even for an empty turn (which returns
                # below without ever reaching STT) and even if whoever is
                # consuming events() stops consuming right here.
                self.last_turn_metrics = {
                    "turn_end_reason": turn_ended.reason,
                    "captured_audio_duration_s": turn_ended.captured_audio_duration_s,
                    "vad_prob_max": turn_ended.vad_prob_max,
                    "vad_prob_mean": turn_ended.vad_prob_mean,
                    "stt_s": None,  # filled in below once STT has actually run
                    "stt_had_text": None,
                }
                yield turn_ended
                if audio.size == 0:
                    continue
                loop = asyncio.get_running_loop()
                self.capture_metrics["stt_attempt_count"] += 1
                result = await loop.run_in_executor(None, self._models.stt.transcribe, audio)
                self.last_turn_metrics["stt_s"] = result.stt_s
                had_text = bool(result.text.strip())
                self.last_turn_metrics["stt_had_text"] = had_text
                if had_text:
                    self.capture_metrics["stt_nonempty_count"] += 1
                # A blank decoder result is still the terminal outcome of one
                # captured turn.  The session driver needs this event to
                # re-arm PTT/wakeword instead of silently waiting for a second
                # utterance in the same activation.
                yield FinalTranscript(text=result.text, lang=result.lang, stt_s=result.stt_s)
            elif self._segmenter.is_speaking:
                buffer.append(frame)
                vad_probs.append(prob)

    async def speak_stream(self, text_iter: AsyncIterator[str], lang: str = "en") -> None:
        """Sentence-chunked streaming TTS — starts speaking before the full LLM
        response is done. Cooperatively stops synthesizing further sentences (but
        cannot itself cancel the caller's LLM-generation task) once abort_playback()
        or a barge-in flips self._speaking to False."""
        assert self._models is not None, "call load() first"
        self._speaking = True
        if self._barge_in_gate is not None:
            self._barge_in_gate.reset()

        aborted = False
        try:
            buffer = ""

            async def _flush(text: str) -> bool:
                clean = sanitize_for_tts(text.strip())
                if not clean:
                    return True
                async for pcm, rate in self._models.tts.synthesize_pcm(clean, lang):
                    if not self._speaking:
                        return False
                    await self._audio_io.play_chunk(pcm, rate)
                return self._speaking

            async for chunk in text_iter:
                if not self._speaking:
                    aborted = True
                    break
                buffer += chunk
                stripped = buffer.rstrip()
                if stripped and stripped[-1] in ".?!\n":
                    if not await _flush(buffer):
                        aborted = True
                        break
                    buffer = ""

            if not aborted and buffer.strip():
                if not await _flush(buffer):
                    aborted = True

            if not aborted:
                await self._audio_io.wait_playback_drained()
        finally:
            self._speaking = False
