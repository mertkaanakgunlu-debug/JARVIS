"""Speech-to-text via faster-whisper. Moved near-verbatim from the pre-Faz-3
voice.py — one-shot transcribe at VAD-detected end-of-turn stays the sole
*authoritative* path (faster-whisper has no real incremental/streaming decode)."""

from __future__ import annotations

import glob
import os
import sysconfig

import numpy as np

from jarvis.config import Settings

# Turkish-specific characters absent from most other languages.
_TR_CHARS = frozenset("şŞğĞüÜöÖçÇıİ")


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

    def transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        """audio: float32 mono samples @16kHz. Returns (text, language_code)."""
        self._ensure_model()
        segments, info = self._model.transcribe(audio, beam_size=5)
        text = " ".join(seg.text for seg in segments).strip()
        lang = info.language
        # Whisper's per-utterance language classifier can mis-fire on short
        # Turkish phrases. A single Turkish-specific character is an
        # unambiguous signal: override to "tr".
        if any(ch in _TR_CHARS for ch in text):
            lang = "tr"
        return text, lang
