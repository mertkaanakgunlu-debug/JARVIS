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
        buffer: list[np.ndarray] = []
        # Rolling tail of the most recent frames, sized to the barge-in gate's
        # own confirmation window. Without this, the frames that built up
        # confidence for a barge-in decision (up to vad_barge_in_duration_s,
        # 0.4s by default) would be silently dropped — the user's interrupting
        # utterance would have its first fraction of a second missing from
        # whatever eventually gets transcribed. Seeded into `buffer` the moment
        # barge-in fires, then the segmenter picks up normally from there.
        barge_in_tail: deque[np.ndarray] = deque(maxlen=self._barge_in_gate.min_frames)

        async for frame in self._audio_io.raw_frames():
            prob = vad.process_chunk(frame)
            yield MicLevel(source="input", rms=self._audio_io.mic_level())

            if self._speaking:
                barge_in_tail.append(frame)
                if self._barge_in_gate.push(prob):
                    self._barge_in_gate.reset()
                    self.abort_playback()
                    buffer = list(barge_in_tail)
                    barge_in_tail.clear()
                    self._segmenter.force_speaking()
                    yield BargeIn()
                continue

            signal = self._segmenter.push(prob)
            if isinstance(signal, SpeechStartedSignal):
                buffer = [frame]
                yield SpeechStarted()
            elif isinstance(signal, TurnEndedSignal):
                audio = np.concatenate(buffer) if buffer else np.array([], dtype=np.float32)
                buffer = []
                if audio.size == 0:
                    continue
                loop = asyncio.get_running_loop()
                text, lang = await loop.run_in_executor(None, self._models.stt.transcribe, audio)
                if text.strip():
                    yield FinalTranscript(text=text, lang=lang)
            elif self._segmenter.is_speaking:
                buffer.append(frame)

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
