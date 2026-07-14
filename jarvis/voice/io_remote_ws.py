"""RemoteWsAudioIO: satisfies the AudioIO protocol (jarvis.voice.io_base) by
carrying audio as binary frames over one /ws connection instead of local
sounddevice hardware — the microphone/speaker are wherever the connected
client is (the Electron HUD today; a phone over Tailscale as a future
fast-follow using the same protocol), not on the machine running the Python
backend.

Wire protocol (see docs/VOICE_PROTOCOL.md for the full spec): JSON control
messages (audio_session_start/stop/ack/nack/end, audio_playback_stop) share
the connection with raw PCM16LE mono binary frames — one binary stream per
direction, no type tag needed inside a frame (client->server binary is always
mic audio; server->client binary is always TTS audio).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import numpy as np
from fastapi import WebSocket

from jarvis.ws import JarvisEventBus

logger = logging.getLogger(__name__)

ENGINE_SAMPLE_RATE = 16000
ENGINE_FRAME_SAMPLES = 512   # must match jarvis.voice.vad.SileroVAD.frame_samples
_TTS_CHUNK_MS = 100          # slice TTS output this small so abort_playback()
                             # takes effect within ~100ms, not a whole sentence


class RemoteWsAudioIO:
    def __init__(self, ws: WebSocket, event_bus: JarvisEventBus, client_sample_rate: int) -> None:
        self._ws = ws
        self._event_bus = event_bus
        self._client_rate = client_sample_rate

        self._input_accum = np.zeros(0, dtype=np.float32)
        self._input_queue: "asyncio.Queue[np.ndarray]" = asyncio.Queue()
        self._last_input_rms = 0.0

        self._playback_aborted = asyncio.Event()
        self._active = True
        # TTS output rate varies per utterance (Piper's Turkish/English voices
        # both happen to be 22050Hz today, but a different voice or the edge-tts
        # cloud fallback can differ) -- announce it whenever it changes, rather
        # than assuming one fixed rate for the whole session at audio_session_ack
        # time (which would be wrong the first time a fallback/other-language
        # utterance used a different rate than the session's opening assumption).
        self._last_announced_rate: int | None = None

    # ── Inbound: called by the /ws route's receive loop for every binary frame ─

    def push_mic_frame(self, data: bytes) -> None:
        """data: raw PCM16LE mono bytes at self._client_rate. Resamples to the
        engine's 16kHz working rate if needed, re-chunks to exactly
        ENGINE_FRAME_SAMPLES, and enqueues each complete frame -- mirrors the
        callback in DuplexAudioIO.start(), just fed from the network instead of
        a PortAudio callback thread."""
        if not self._active or not data:
            return
        pcm16 = np.frombuffer(data, dtype="<i2")
        floats = pcm16.astype(np.float32) / 32768.0

        if self._client_rate != ENGINE_SAMPLE_RATE:
            from scipy.signal import resample_poly
            floats = resample_poly(floats, ENGINE_SAMPLE_RATE, self._client_rate).astype(np.float32)

        self._input_accum = np.concatenate([self._input_accum, floats])
        while len(self._input_accum) >= ENGINE_FRAME_SAMPLES:
            frame = self._input_accum[:ENGINE_FRAME_SAMPLES]
            self._input_accum = self._input_accum[ENGINE_FRAME_SAMPLES:]
            self._last_input_rms = float(np.sqrt(np.mean(frame ** 2))) if frame.size else 0.0
            self._input_queue.put_nowait(frame)

    # ── AudioIO protocol ──────────────────────────────────────────────────────

    async def start(self) -> None:
        pass  # nothing to open -- the /ws connection is already live

    async def stop(self) -> None:
        self._active = False
        self._input_queue.put_nowait(np.zeros(0, dtype=np.float32))  # unblock raw_frames()

    async def raw_frames(self) -> AsyncIterator[np.ndarray]:
        while self._active:
            frame = await self._input_queue.get()
            if not self._active:
                return
            yield frame

    def mic_level(self) -> float:
        return self._last_input_rms

    async def play_chunk(self, pcm: np.ndarray, sample_rate: int) -> None:
        self._playback_aborted.clear()
        if sample_rate != self._last_announced_rate:
            await self._event_bus.send_text_to(
                self._ws, {"type": "audio_format", "sample_rate": sample_rate}
            )
            self._last_announced_rate = sample_rate
        chunk_samples = max(1, int(sample_rate * _TTS_CHUNK_MS / 1000))
        pcm16 = (np.clip(pcm, -1.0, 1.0) * 32767).astype("<i2")
        for i in range(0, len(pcm16), chunk_samples):
            if self._playback_aborted.is_set():
                return
            piece = pcm16[i:i + chunk_samples]
            await self._event_bus.send_bytes_to(self._ws, piece.tobytes())

    async def wait_playback_drained(self) -> None:
        # send_bytes_to() enqueues onto the connection's own writer task; there's
        # no further "left the wire yet" signal to poll (unlike DuplexAudioIO,
        # which drains a local ring buffer) -- handoff to that background writer
        # is near-instant relative to audio playback timing, so a small fixed
        # pause is enough.
        await asyncio.sleep(0.05)

    def abort_playback(self) -> None:
        self._playback_aborted.set()
        # Tell the client to stop playing whatever it already buffered — this
        # method is sync (the AudioIO contract), so schedule the send rather
        # than awaiting it; we're on the same event loop as the connection's
        # writer task, just not inside a coroutine here.
        asyncio.ensure_future(self._event_bus.send_text_to(self._ws, {"type": "audio_playback_stop"}))
