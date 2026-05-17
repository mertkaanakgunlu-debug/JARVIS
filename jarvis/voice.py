"""Voice I/O engine: energy-VAD recording, Faster-Whisper STT, edge-tts TTS."""

from __future__ import annotations

import asyncio
import difflib
import re
import unicodedata
from collections.abc import AsyncIterator

import numpy as np
import sounddevice as sd

from jarvis.config import Settings


# ── TTS text sanitizer ────────────────────────────────────────────────────────

def sanitize_for_tts(text: str) -> str:
    """Strip markdown and non-speakable characters before sending to TTS.

    edge-tts reads asterisks, hash signs, and emoji descriptions verbatim,
    producing robot-like output.  This function converts to clean spoken prose.
    """
    # Bold / italic
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\*(.+?)\*',     r'\1', text, flags=re.DOTALL)
    text = re.sub(r'__(.+?)__',     r'\1', text, flags=re.DOTALL)
    text = re.sub(r'_(.+?)_',       r'\1', text, flags=re.DOTALL)
    # Headers
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Inline code
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Markdown links → anchor text only
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Bullet / numbered list markers
    text = re.sub(r'^\s*[-•*]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)
    # Horizontal rules
    text = re.sub(r'^[-=_]{3,}$', '', text, flags=re.MULTILINE)
    # Emojis (Unicode So + Sm + supplemental pictographs)
    text = ''.join(
        c for c in text
        if not (unicodedata.category(c) in ('So', 'Sm') or ord(c) > 0x1F000)
    )
    # Ellipsis → natural pause comma
    text = text.replace('…', ',').replace('...', ',')
    # Strip any remaining bare markdown symbols (* # _ ~)
    text = re.sub(r'[*#_~]+', '', text)
    # Paragraph breaks → sentence boundary
    text = re.sub(r'\n{2,}', '. ', text)
    text = text.replace('\n', ' ')
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


# ── Exit phrase detection ─────────────────────────────────────────────────────

_EXIT_EN = {
    "exit", "quit", "goodbye", "bye", "bye bye", "see you", "see ya",
    "see you later", "that's all", "shut down", "stop",
}
_EXIT_TR = {
    "güle güle", "gule gule", "hoşçakal", "hoscakal", "hoşça kal",
    "hosca kal", "görüşürüz", "gorusuruz", "sonra görüşürüz",
    "sonra gorusuruz", "çıkış", "cikis", "kapat", "yeter", "tamam kapat",
}
_EXIT_PHRASES = _EXIT_EN | _EXIT_TR


def _normalize(s: str) -> str:
    return s.strip().lower().rstrip(".,!?")


def is_exit_phrase(text: str) -> bool:
    """Return True when the transcribed text is a farewell/exit utterance.

    Uses fuzzy matching (0.82 similarity threshold) to catch STT variants like
    "gulei gulei" → "gule gule" or "goodbyes" → "goodbye".
    """
    norm = _normalize(text)
    for phrase in _EXIT_PHRASES:
        if norm == phrase or norm.startswith(phrase + " "):
            return True
        # Fuzzy match: STT often mis-transcribes non-English exit phrases.
        if difflib.SequenceMatcher(None, norm, phrase).ratio() >= 0.82:
            return True
    return False


# ── Voice selection ───────────────────────────────────────────────────────────

_VOICES: dict[str, str] = {
    "tr": "tr-TR-AhmetNeural",
    "en": "en-US-ChristopherNeural",   # deep, authoritative — closest to JARVIS
    "de": "de-DE-ConradNeural",
    "fr": "fr-FR-HenriNeural",
    "es": "es-ES-AlvaroNeural",
    "it": "it-IT-DiegoNeural",
    "pt": "pt-BR-AntonioNeural",
    "ru": "ru-RU-DmitryNeural",
    "zh": "zh-CN-YunxiNeural",
    "ja": "ja-JP-KeitaNeural",
    "ar": "ar-SA-HamedNeural",
    "ko": "ko-KR-InJoonNeural",
    "nl": "nl-NL-MaartenNeural",
    "pl": "pl-PL-MarekNeural",
    "sv": "sv-SE-MattiasNeural",
}
_DEFAULT_VOICE = "en-US-ChristopherNeural"


# ── Engine ────────────────────────────────────────────────────────────────────

class VoiceEngine:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._oww_model = None          # openwakeword model (lazy)
        self._device: str | None = None
        self._sample_rate = 16000
        self._chunk_ms = settings.voice_chunk_ms
        self._silence_threshold = 0.005                       # RMS energy below this = silence
        self._silence_duration = settings.voice_silence_duration  # seconds of consecutive silence → end
        self._max_duration = 30                               # hard cap in seconds

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self) -> None:
        """Pre-load the Whisper model (first run downloads ~800 MB)."""
        self._ensure_model()

    def load_wakeword(self) -> bool:
        """Pre-load the hey_jarvis openwakeword model. Returns True on success."""
        return self._ensure_oww_model()

    def listen_for_wakeword(self, threshold: float = 0.5, stop_event=None) -> bool:
        """Block until 'Hey JARVIS' is detected. Returns True on activation.

        Falls back silently (returns True immediately) if openwakeword is
        unavailable — the voice loop continues without wake-word gating.

        stop_event: optional threading.Event; if set(), returns False immediately
        (used by voice_api.py to interrupt wakeword listening on PTT press).
        """
        if not self._ensure_oww_model():
            return True  # graceful degradation

        _OWW_CHUNK = 1280  # 80ms at 16kHz (required by openwakeword)

        with sd.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="int16",
            blocksize=_OWW_CHUNK,
        ) as stream:
            while True:
                if stop_event is not None and stop_event.is_set():
                    return False  # interrupted by PTT
                data, _ = stream.read(_OWW_CHUNK)
                chunk = data.flatten().astype(np.float32) / 32768.0
                prediction = self._oww_model.predict(chunk)
                for score in prediction.values():
                    if score >= threshold:
                        return True

    def listen(self) -> tuple[str, str]:
        """Block until speech is detected, record, transcribe.

        Returns (text, language_code).  Runs synchronously — call from a
        thread-pool executor to keep the asyncio event loop free.
        """
        audio = self._record()
        if audio.size == 0:
            return "", "en"
        return self._transcribe(audio)

    async def speak(self, text: str, lang: str = "en") -> None:
        """Synthesise text with edge-tts and play through the default speaker."""
        if not text.strip():
            return
        import edge_tts
        import miniaudio

        voice = _VOICES.get(lang[:2], _DEFAULT_VOICE)
        communicate = edge_tts.Communicate(text, voice)

        mp3_bytes = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_bytes.extend(chunk["data"])

        if not mp3_bytes:
            return

        decoded = miniaudio.decode(
            bytes(mp3_bytes),
            output_format=miniaudio.SampleFormat.FLOAT32,
        )
        audio_array = np.frombuffer(decoded.samples, dtype=np.float32)
        # edge-tts delivers stereo; mix down to mono so sounddevice plays at
        # the correct speed and pitch (stereo as flat mono = half speed).
        if decoded.nchannels > 1:
            audio_array = audio_array.reshape(-1, decoded.nchannels).mean(axis=1)

        loop = asyncio.get_running_loop()
        sd.play(audio_array, decoded.sample_rate)
        await loop.run_in_executor(None, sd.wait)

    async def speak_stream(
        self,
        text_iter: AsyncIterator[str],
        lang: str = "en",
    ) -> None:
        """Sentence-chunked streaming TTS: JARVIS starts speaking before generation ends.

        Collects token deltas, flushes on sentence boundaries (.?!\\n), and
        plays each sentence through a queue so playback overlaps with generation.
        """
        queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def player() -> None:
            while True:
                sentence = await queue.get()
                if sentence is None:
                    break
                await self.speak(sentence, lang)

        player_task = asyncio.create_task(player())

        buffer = ""
        async for chunk in text_iter:
            buffer += chunk
            stripped = buffer.rstrip()
            if stripped and stripped[-1] in ".?!\n":
                clean = sanitize_for_tts(buffer.strip())
                if clean:
                    await queue.put(clean)
                buffer = ""

        if buffer.strip():
            clean = sanitize_for_tts(buffer.strip())
            if clean:
                await queue.put(clean)

        await queue.put(None)
        await player_task

    # ── Internals ─────────────────────────────────────────────────────────────

    def _ensure_oww_model(self) -> bool:
        """Lazily load the hey_jarvis openwakeword ONNX model. Returns True on success."""
        if self._oww_model is not None:
            return True
        try:
            from openwakeword.model import Model

            self._oww_model = Model(
                wakeword_models=["hey_jarvis"],
                inference_framework="onnx",
            )
            return True
        except Exception:
            return False

    @staticmethod
    def _register_cuda_dll_dirs() -> None:
        """Prepend pip-installed nvidia CUDA DLL directories to PATH.

        Packages like nvidia-cublas-cu12 install DLLs under
        site-packages/nvidia/<pkg>/bin/ but Windows won't find them unless the
        directory is on PATH. Must be called before WhisperModel is loaded.
        """
        import os
        import glob
        import sysconfig
        site_pkg = sysconfig.get_path("purelib")
        dirs = [
            os.path.normpath(p)
            for p in glob.glob(f"{site_pkg}/nvidia/*/bin")
        ]
        if dirs:
            os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")

    def _ensure_model(self) -> None:
        if self._model is not None:
            return

        device = self.settings.whisper_device          # "auto" | "cuda" | "cpu"
        compute = self.settings.whisper_compute_type   # "auto" | "int8" | ...

        # Register pip-installed CUDA DLL dirs before importing faster_whisper /
        # ctranslate2 so Windows can find cublas64_12.dll etc.
        if device != "cpu":
            self._register_cuda_dll_dirs()

        from faster_whisper import WhisperModel

        def _load(dev: str, ctype: str) -> "WhisperModel":
            resolved_ctype = "int8" if (ctype == "auto" and dev == "cpu") else ctype
            model = WhisperModel("large-v3-turbo", device=dev, compute_type=resolved_ctype)
            # Force-run a tiny encode to surface CUDA errors now, not mid-speech.
            list(model.transcribe(np.zeros(1600, dtype=np.float32), beam_size=1)[0])
            return model

        _CUDA_ERRORS = ("cublas", "cudnn", "cuda", "libcuda", "cufft", "curand")

        if device == "cpu":
            self._model = _load("cpu", compute)
            self._device = "cpu"
            return

        try:
            self._model = _load(device, compute)
            self._device = device
        except RuntimeError as exc:
            if any(kw in str(exc).lower() for kw in _CUDA_ERRORS):
                print(
                    "\n[JARVIS] CUDA libraries unavailable — falling back to CPU. "
                    "(Set WHISPER_DEVICE=cpu in .env to silence this warning.)"
                )
                self._model = _load("cpu", "int8")
                self._device = "cpu"
            else:
                raise

    def _record(self) -> np.ndarray:
        """Energy-based VAD recording.

        Phase 1 — wait for speech start (RMS > threshold).
        Phase 2 — accumulate frames; stop after silence_duration of quiet.
        """
        chunk_samples = int(self._sample_rate * self._chunk_ms / 1000)
        silence_needed = int(self._silence_duration * 1000 / self._chunk_ms)
        max_chunks = int(self._max_duration * 1000 / self._chunk_ms)

        frames: list[np.ndarray] = []
        silent_count = 0
        speaking = False

        with sd.InputStream(
            samplerate=self._sample_rate,
            channels=1,
            dtype="float32",
            blocksize=chunk_samples,
        ) as stream:
            while len(frames) < max_chunks:
                data, _ = stream.read(chunk_samples)
                chunk = data.flatten()
                rms = float(np.sqrt(np.mean(chunk ** 2)))

                if not speaking:
                    if rms > self._silence_threshold:
                        speaking = True
                        frames.append(chunk)
                else:
                    frames.append(chunk)
                    if rms < self._silence_threshold:
                        silent_count += 1
                        if silent_count >= silence_needed:
                            break
                    else:
                        silent_count = 0

        return np.concatenate(frames) if frames else np.array([], dtype=np.float32)

    # Turkish-specific characters absent from most other languages.
    _TR_CHARS = frozenset("şŞğĞüÜöÖçÇıİ")

    def _transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        self._ensure_model()
        segments, info = self._model.transcribe(audio, beam_size=5)
        text = " ".join(seg.text for seg in segments).strip()
        lang = info.language
        # Whisper's per-utterance language classifier can mis-fire on short
        # Turkish phrases.  A single Turkish-specific character is an
        # unambiguous signal: override to "tr".
        if any(ch in self._TR_CHARS for ch in text):
            lang = "tr"
        return text, lang
