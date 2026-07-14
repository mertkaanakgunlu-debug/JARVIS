"""Local TTS via Piper (primary) with cloud edge-tts kept as an optional fallback
tier — mirrors this project's existing local-first-not-cloud-forbidden pattern
from the Faz 1 LLM router (jarvis/providers/).

Piper chosen over Kokoro-82M specifically because Kokoro does not support Turkish
(confirmed: its language list is English/Spanish/French/Hindi/Italian/Japanese/
Portuguese/Mandarin only) while Piper has a Turkish voice (tr_TR-dfki-medium,
confirmed live against the real rhasspy/piper-voices voices.json — earlier
candidate names like "fahrettin"/"fettah" are not currently published there).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from jarvis.config import Settings

if TYPE_CHECKING:
    from piper import PiperVoice

logger = logging.getLogger(__name__)

# Voice per language code — only languages with a real, confirmed Piper voice are
# listed; any other language falls through to CloudTTSFallback (edge-tts).
_PIPER_VOICE_NAMES: dict[str, str] = {
    "tr": "tr_TR-dfki-medium",
    "en": "en_US-lessac-medium",
}

_PIPER_CACHE_DIR = Path.home() / ".cache" / "jarvis" / "piper_voices"

# ── edge-tts voice map (unchanged from the pre-Faz-3 voice.py) ────────────────
_EDGE_VOICES: dict[str, str] = {
    "tr": "tr-TR-AhmetNeural",
    "en": "en-US-ChristopherNeural",
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
_EDGE_DEFAULT_VOICE = "en-US-ChristopherNeural"


class PiperEngine:
    """Local Piper TTS. Voice models auto-download on first use (same lazy-cache
    pattern as faster-whisper/marker-pdf's existing model downloads)."""

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or _PIPER_CACHE_DIR
        self._voices: dict[str, "PiperVoice"] = {}

    def supports(self, lang: str) -> bool:
        return lang[:2] in _PIPER_VOICE_NAMES

    def _ensure_voice(self, lang: str) -> "PiperVoice":
        code = lang[:2]
        if code in self._voices:
            return self._voices[code]
        voice_name = _PIPER_VOICE_NAMES[code]

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        model_path = self._cache_dir / f"{voice_name}.onnx"
        config_path = self._cache_dir / f"{voice_name}.onnx.json"

        from piper import PiperVoice
        from piper.download_voices import download_voice

        if not model_path.exists() or not config_path.exists():
            logger.info("[voice] Downloading Piper voice '%s' (one-time)...", voice_name)
            download_voice(voice_name, self._cache_dir)

        voice = PiperVoice.load(model_path, config_path)
        self._voices[code] = voice
        return voice

    def synthesize_pcm_sync(self, text: str, lang: str) -> list[tuple[np.ndarray, int]]:
        """Blocking — call via run_in_executor. Returns [(mono float32 chunk, sample_rate), ...]."""
        voice = self._ensure_voice(lang)
        chunks: list[tuple[np.ndarray, int]] = []
        for audio_chunk in voice.synthesize(text):
            arr = audio_chunk.audio_float_array
            if audio_chunk.sample_channels > 1:
                arr = arr.reshape(-1, audio_chunk.sample_channels).mean(axis=1)
            chunks.append((arr.astype(np.float32), audio_chunk.sample_rate))
        return chunks


class CloudTTSFallback:
    """edge-tts + miniaudio — moved verbatim from the pre-Faz-3 VoiceEngine.speak(),
    reshaped to return PCM instead of playing it directly (playback is owned by
    the AudioIO layer, not the TTS engine, so both engines share one interface)."""

    async def synthesize(self, text: str, lang: str) -> tuple[np.ndarray, int]:
        import edge_tts
        import miniaudio

        voice = _EDGE_VOICES.get(lang[:2], _EDGE_DEFAULT_VOICE)
        communicate = edge_tts.Communicate(text, voice)

        mp3_bytes = bytearray()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_bytes.extend(chunk["data"])

        if not mp3_bytes:
            return np.array([], dtype=np.float32), 24000

        decoded = miniaudio.decode(
            bytes(mp3_bytes),
            output_format=miniaudio.SampleFormat.FLOAT32,
        )
        audio_array = np.frombuffer(decoded.samples, dtype=np.float32)
        # edge-tts delivers stereo; mix down to mono so playback is the correct
        # speed/pitch (stereo samples played back as flat mono = half speed).
        if decoded.nchannels > 1:
            audio_array = audio_array.reshape(-1, decoded.nchannels).mean(axis=1)
        return audio_array, decoded.sample_rate


class TtsRouter:
    """Selects Piper vs. cloud fallback per language, and normalizes both engines'
    sync/async mismatch into one async interface for RealtimeVoiceEngine to consume."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._piper = PiperEngine() if settings.tts_engine == "piper" else None
        self._cloud = CloudTTSFallback()

    async def synthesize_pcm(self, text: str, lang: str) -> AsyncIterator[tuple[np.ndarray, int]]:
        """Yields (mono float32 chunk, sample_rate) for one sentence/utterance."""
        if self._piper is not None and self._piper.supports(lang):
            loop = asyncio.get_running_loop()
            chunks = await loop.run_in_executor(None, self._piper.synthesize_pcm_sync, text, lang)
            for c in chunks:
                yield c
            return

        if not self._settings.tts_allow_cloud_fallback:
            logger.warning("[voice] No Piper voice for lang=%s and cloud fallback disabled — skipping TTS.", lang)
            return

        arr, rate = await self._cloud.synthesize(text, lang)
        if arr.size:
            yield arr, rate
