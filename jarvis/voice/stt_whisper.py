"""Speech-to-text via faster-whisper. Moved near-verbatim from the pre-Faz-3
voice.py — one-shot transcribe at VAD-detected end-of-turn stays the sole
*authoritative* path (faster-whisper has no real incremental/streaming decode)."""

from __future__ import annotations

import glob
import logging
import os
import sysconfig
import time
from dataclasses import dataclass

import numpy as np

from jarvis.config import Settings

logger = logging.getLogger(__name__)

# Turkish-specific characters absent from most other languages.
_TR_CHARS = frozenset("şŞğĞüÜöÖçÇıİ")


@dataclass(frozen=True)
class TranscriptionResult:
    """Faz F (WAV replay harness): stt_s was already computed for the [stt]
    log line but discarded otherwise -- a dataclass return (not a growing
    positional tuple) so a future field doesn't silently reorder every
    existing text, lang = ... call site into a bug."""
    text: str
    lang: str
    stt_s: float


class WhisperSTT:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None
        self._device: str | None = None

    @staticmethod
    def _register_cuda_dll_dirs() -> None:
        """Prepend pip-installed nvidia CUDA DLL directories to PATH.

        Packages like nvidia-cublas-cu12 install DLLs under
        site-packages/nvidia/<pkg>/bin/ but Windows won't find them unless the
        directory is on PATH. Must be called before WhisperModel is loaded.
        """
        site_pkg = sysconfig.get_path("purelib")
        dirs = [
            os.path.normpath(p)
            for p in glob.glob(f"{site_pkg}/nvidia/*/bin")
        ]
        if dirs:
            os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + os.environ.get("PATH", "")

    def load(self) -> None:
        """Pre-load the Whisper model (first run downloads ~800 MB)."""
        self._ensure_model()

    def _ensure_model(self) -> None:
        if self._model is not None:
            return

        device = self.settings.whisper_device          # "auto" | "cuda" | "cpu"
        compute = self.settings.whisper_compute_type    # "auto" | "int8" | ...

        # Register pip-installed CUDA DLL dirs before importing faster_whisper /
        # ctranslate2 so Windows can find cublas64_12.dll etc.
        if device != "cpu":
            self._register_cuda_dll_dirs()

        from faster_whisper import WhisperModel

        model_id = self.settings.whisper_model

        def _load(dev: str, ctype: str) -> "WhisperModel":
            resolved_ctype = "int8" if (ctype == "auto" and dev == "cpu") else ctype
            model = WhisperModel(model_id, device=dev, compute_type=resolved_ctype)
            # Force-run a tiny encode to surface CUDA errors now, not mid-speech.
            list(model.transcribe(np.zeros(1600, dtype=np.float32), beam_size=1)[0])
            return model

        _CUDA_ERRORS = ("cublas", "cudnn", "cuda", "libcuda", "cufft", "curand")

        if device == "cpu":
            self._model = _load("cpu", compute)
            self._device = self._actual_device(self._model, "cpu")
            return

        try:
            self._model = _load(device, compute)
            self._device = self._actual_device(self._model, device)
        except RuntimeError as exc:
            if any(kw in str(exc).lower() for kw in _CUDA_ERRORS):
                print(
                    "\n[JARVIS] CUDA libraries unavailable — falling back to CPU. "
                    "(Set WHISPER_DEVICE=cpu in .env to silence this warning.)"
                )
                self._model = _load("cpu", "int8")
                self._device = self._actual_device(self._model, "cpu")
            else:
                raise

    @staticmethod
    def _actual_device(model, requested: str) -> str:
        """The device the model actually loaded on -- requested may be "auto",
        which resolves to "cuda"/"cpu" under the hood. Read from ctranslate2's
        own Whisper model so telemetry never reports "auto"."""
        try:
            return model.model.device  # faster_whisper.WhisperModel -> ct2 Whisper.device
        except Exception:
            return requested

    def transcribe(self, audio: np.ndarray) -> TranscriptionResult:
        """audio: float32 mono samples @16kHz."""
        self._ensure_model()
        # Decoder language lock (review remediation): passing language= stops
        # Whisper's per-utterance classifier from mis-firing on short Turkish
        # phrases (the live `разденьемся` incident, where a Turkish command was
        # decoded as Russian). "auto" preserves the old auto-detect behavior.
        forced = self.settings.whisper_language
        language = None if forced == "auto" else forced
        beam_size = self.settings.whisper_beam_size

        t0 = time.monotonic()
        segments, info = self._model.transcribe(audio, beam_size=beam_size, language=language)
        text = " ".join(seg.text for seg in segments).strip()
        lang = info.language
        stt_s = time.monotonic() - t0
        # Only run the Turkish-character metadata backstop when we did NOT force a
        # language -- a forced "en" session must not be silently re-tagged "tr".
        if language is None and any(ch in _TR_CHARS for ch in text):
            lang = "tr"

        audio_s = len(audio) / 16000 if len(audio) else 0.0
        rtf = (stt_s / audio_s) if audio_s else 0.0
        logger.info(
            "[stt] model=%s requested_device=%s actual_device=%s compute=%s beam=%d "
            "language=%s audio=%.2fs stt=%.3fs rtf=%.3f",
            self.settings.whisper_model, self.settings.whisper_device, self._device,
            self.settings.whisper_compute_type, beam_size, forced, audio_s, stt_s, rtf,
        )
        return TranscriptionResult(text=text, lang=lang, stt_s=stt_s)
