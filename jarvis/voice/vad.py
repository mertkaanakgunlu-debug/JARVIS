"""Voice activity detection: pure turn-taking logic + a raw-ONNX Silero VAD wrapper.

Silero VAD is loaded from its raw .onnx weights via onnxruntime, NOT the
`silero-vad` pip package — that package hard-requires torch/torchaudio in its
own pyproject.toml even though inference can run ONNX-only, and this project
has deliberately never needed torch (see MEMORY.md's VRAM budget notes).
"""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Union

import numpy as np

logger = logging.getLogger(__name__)

# Pinned to a specific commit rather than tracking `master`. IMPORTANT: this is
# v5.1.2, not the newer v6.2.1 — verified empirically (not assumed), both against
# real Piper-synthesized speech: v6.2.1's exported .onnx graph declares the exact
# same input/output names and shapes as v5.1.2 (input/state/sr -> output/stateN)
# but with this standard frame-by-frame streaming calling convention it never
# produces a usable speech-probability signal (max ~0.33 across a 3s spoken
# sentence, silence and real speech both score near-zero). v5.1.2 discriminates
# correctly (silence/noise max ~0.04-0.12, real speech mean ~0.85-0.93 for both
# English and Turkish test utterances). Re-verify before ever bumping this pin.
_SILERO_VAD_COMMIT = "6478567951ae5c9979ad7b234185b5515f4be7a1"  # tag v5.1.2
_SILERO_VAD_URL = (
    f"https://raw.githubusercontent.com/snakers4/silero-vad/"
    f"{_SILERO_VAD_COMMIT}/src/silero_vad/data/silero_vad.onnx"
)


def _default_cache_path() -> Path:
    """Agent Runtime rev.2, Faz 5: resolved per call via jarvis.paths.cache_dir()
    (JARVIS_HOME-aware), not a Path.home()-based module constant frozen at
    import time -- the prior form bypassed test/eval isolation entirely."""
    from jarvis.paths import cache_dir
    return cache_dir() / "silero_vad.onnx"


def ensure_silero_vad_model(cache_path: Path | None = None) -> Path:
    """Download Silero VAD's .onnx weights on first use (same lazy-cache pattern
    as faster-whisper/marker-pdf's existing model downloads). Returns the local path."""
    path = cache_path or _default_cache_path()
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    logger.info("[voice] Downloading Silero VAD model (~2 MB, one-time)...")
    tmp = path.with_suffix(".onnx.tmp")
    urllib.request.urlretrieve(_SILERO_VAD_URL, tmp)
    tmp.replace(path)
    return path


# ── Pure logic — no audio/model dependency, trivially unit-testable ───────────

class SustainedGate:
    """Fires exactly once after >= min_frames consecutive push()es on the
    configured side of threshold. Reused for speech-start detection,
    end-of-silence detection, and (as a separately-configured instance) the
    barge-in gate — same primitive, different thresholds/durations."""

    def __init__(self, threshold: float, min_frames: int, above: bool = True) -> None:
        self.threshold = threshold
        self.min_frames = max(1, min_frames)
        self.above = above
        self._count = 0
        self._fired = False

    def push(self, value: float) -> bool:
        satisfied = (value >= self.threshold) if self.above else (value < self.threshold)
        self._count = self._count + 1 if satisfied else 0
        if self._count >= self.min_frames and not self._fired:
            self._fired = True
            return True
        return False

    def reset(self) -> None:
        self._count = 0
        self._fired = False


@dataclass(frozen=True)
class SpeechStartedSignal:
    """VAD-layer signal: speech just started. Distinct from voice.events.SpeechStarted
    (that one is the engine-level event surfaced to callers) to keep this module
    free of any engine/orchestration dependency."""


@dataclass(frozen=True)
class TurnEndedSignal:
    """VAD-layer signal: sustained silence (or the hard duration cap) ended the
    current utterance — caller should now run STT on the accumulated buffer."""
    reason: Literal["silence", "max_duration"]


VadSignal = Union[SpeechStartedSignal, TurnEndedSignal, None]


class VadTurnSegmenter:
    """Turn-taking state machine driven by per-frame speech probability (replaces
    the energy/RMS-threshold state machine in the old voice.py's _record())."""

    def __init__(
        self,
        *,
        speech_threshold: float = 0.5,
        silence_duration_s: float = 1.5,
        frame_duration_s: float = 0.032,  # Silero's 512-sample frame @ 16kHz
        max_duration_s: float = 30.0,
    ) -> None:
        self._frame_duration_s = frame_duration_s
        min_silence_frames = max(1, round(silence_duration_s / frame_duration_s))
        self._max_frames = max(1, round(max_duration_s / frame_duration_s))
        # A single above-threshold frame starts accumulation — matches the old
        # energy-VAD's "first loud frame" behavior. Sustained discipline matters
        # for *ending* a turn (below), not starting one.
        self._start_gate = SustainedGate(speech_threshold, min_frames=1, above=True)
        self._silence_gate = SustainedGate(speech_threshold, min_frames=min_silence_frames, above=False)
        self._speaking = False
        self._frame_count = 0

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def force_speaking(self) -> None:
        """Mark speech as already in progress without emitting SpeechStartedSignal
        — used when a barge-in (a separately-gated, higher-confidence detector
        that runs while this segmenter is idle-by-design, see RealtimeVoiceEngine)
        has already confirmed speech started; the caller seeds its own buffer
        with the frames that led to that confirmation and just needs this
        segmenter to resume normal end-of-turn tracking from here."""
        self._speaking = True
        self._frame_count = 0
        self._silence_gate.reset()

    def push(self, prob: float) -> VadSignal:
        if not self._speaking:
            if self._start_gate.push(prob):
                self._speaking = True
                self._frame_count = 0
                self._silence_gate.reset()
                return SpeechStartedSignal()
            return None

        self._frame_count += 1
        if self._frame_count >= self._max_frames:
            self.reset()
            return TurnEndedSignal(reason="max_duration")

        if self._silence_gate.push(prob):
            self.reset()
            return TurnEndedSignal(reason="silence")

        return None

    def reset(self) -> None:
        self._speaking = False
        self._frame_count = 0
        self._start_gate.reset()
        self._silence_gate.reset()


# ── Model wrapper ───────────────────────────────────────────────────────────────

class SileroVAD:
    """Raw-ONNX Silero VAD wrapper. Frame width is read from the loaded model's
    own declared input shape when it's a concrete integer; Silero's public graph
    declares a dynamic (batch, frame) shape, so this normally falls back to the
    documented default of 512 samples (32ms @ 16kHz) — verified live against the
    actual released model, not assumed."""

    FRAME_SAMPLES = 512

    def __init__(self, model_path: str | Path, sample_rate: int = 16000) -> None:
        import onnxruntime as ort

        self._session = ort.InferenceSession(str(model_path), providers=["CPUExecutionProvider"])
        self._input_names = [i.name for i in self._session.get_inputs()]
        self._output_names = [o.name for o in self._session.get_outputs()]

        frame_input = next((i for i in self._session.get_inputs() if i.name == "input"), None)
        frame_dim = frame_input.shape[-1] if frame_input is not None else None
        self.frame_samples = frame_dim if isinstance(frame_dim, int) else self.FRAME_SAMPLES

        self._sample_rate = sample_rate
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def reset_states(self) -> None:
        """Zero the recurrent state — call at the start of each listening session
        (same fix shape as the openwakeword buffer-reset fix, BUG-23)."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)

    def process_chunk(self, chunk: np.ndarray) -> float:
        """chunk: float32 mono samples, exactly self.frame_samples long. Returns
        the speech probability for this frame."""
        if chunk.shape[0] != self.frame_samples:
            raise ValueError(
                f"SileroVAD expects exactly {self.frame_samples} samples, got {chunk.shape[0]}"
            )
        feed: dict[str, np.ndarray] = {}
        for name in self._input_names:
            if name == "sr":
                feed[name] = np.array(self._sample_rate, dtype=np.int64)
            elif "state" in name:
                feed[name] = self._state
            else:
                feed[name] = chunk.reshape(1, -1).astype(np.float32)
        outputs = self._session.run(self._output_names, feed)
        prob = float(np.asarray(outputs[0]).reshape(-1)[0])
        if len(outputs) > 1:
            self._state = outputs[1]
        return prob
