"""Wake-word detection ("Hey JARVIS") via openwakeword. Moved from the pre-Faz-3
voice.py, with BUG-23 fixed: the model's internal prediction/mel-spectrogram
buffers are now reset at the start of each listening session instead of never."""

from __future__ import annotations

import numpy as np
import sounddevice as sd

_OWW_CHUNK = 1280  # 80ms at 16kHz (required by openwakeword)
_SAMPLE_RATE = 16000


class WakewordDetector:
    def __init__(self) -> None:
        self._model = None

    def load(self) -> bool:
        """Lazily load the hey_jarvis openwakeword ONNX model. Returns True on success."""
        if self._model is not None:
            return True
        try:
            from openwakeword.model import Model

            self._model = Model(
                wakeword_models=["hey_jarvis"],
                inference_framework="onnx",
            )
            return True
        except Exception:
            return False

    def listen(self, threshold: float = 0.5, stop_event=None) -> bool:
        """Block until 'Hey JARVIS' is detected. Returns True on activation.

        Falls back silently (returns True immediately) if openwakeword is
        unavailable — the voice loop continues without wake-word gating.

        stop_event: optional threading.Event; if set(), returns False immediately
        (used to interrupt wakeword listening on PTT press).
        """
        if not self.load():
            return True  # graceful degradation

        # BUG-23 fix: without this, the model's internal prediction_buffer and
        # mel-spectrogram preprocessor buffer carry state across sessions —
        # audio from a previous listen() call could still influence this one's
        # first predictions.
        self._model.reset()

        with sd.InputStream(
            samplerate=_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=_OWW_CHUNK,
        ) as stream:
            while True:
                if stop_event is not None and stop_event.is_set():
                    return False  # interrupted by PTT
                data, _ = stream.read(_OWW_CHUNK)
                chunk = data.flatten().astype(np.float32) / 32768.0
                prediction = self._model.predict(chunk)
                for score in prediction.values():
                    if score >= threshold:
                        return True
