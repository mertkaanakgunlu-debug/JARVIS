"""One voice diagnostics snapshot, shared by every surface that reports it
-- the CLI's startup print and its `/voice-status` command, and the API's
authenticated GET /voice/status.

Faz D surfaced the individual telemetry values (DuplexAudioIO's
queue_depth()/underrun_count/input_* counters, the engine's per-turn VAD
probabilities, the real device Whisper loaded onto) but never assembled
them: the only reader was cli.py's startup print, which showed four static
fields and nothing live. So "is capture keeping up right now?" -- the
question the counters exist to answer -- had no answer short of reading
logs. This module is that assembly point, written once so the CLI and the
API cannot drift into reporting different things.

The live-session registry exists because the natural owner of a snapshot
is whichever voice loop is currently running, and that loop is a background
task (voice_api._voice_loop, api.py's /ws session) with no reference the
request handler could otherwise reach. Registration is per-process and
last-writer-wins: exactly one voice session runs at a time (enforced
upstream by jarvis/voice/session_manager.py's claim), so there is nothing
to multiplex.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class VoiceDiagnosticsSnapshot:
    """Everything a "why is voice misbehaving?" question needs, in one object.

    Every field is best-effort: a diagnostics read must never be the thing
    that breaks a voice session, so a missing attribute degrades to None /
    "unavailable" rather than raising. Booleans and counters stay separate
    from the human-readable device strings so the API can serve this as
    JSON without any reformatting.
    """
    # Devices / fixed configuration
    input_device: str = "unknown"
    output_device: str = "unknown"
    sample_rate: "int | None" = None
    stt_device: str = "not loaded"

    # Live capture health
    mic_rms: float = 0.0
    queue_depth: "int | None" = None
    input_status_count: "int | None" = None
    input_overflow_count: "int | None" = None
    output_underrun_count: "int | None" = None

    # Last completed turn (None until one has completed)
    last_vad_prob_max: "float | None" = None
    last_vad_prob_mean: "float | None" = None
    last_turn_end_reason: "str | None" = None
    last_captured_audio_s: "float | None" = None
    last_stt_s: "float | None" = None

    # Reducer (jarvis/voice/state.py). None when no session is running.
    capture: "str | None" = None
    response: "str | None" = None
    display: "str | None" = None
    hud_state: "str | None" = None

    session_active: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _safe(getter, default=None):
    try:
        return getter()
    except Exception:  # noqa: BLE001 -- diagnostics must never raise
        return default


def _device_name(selector, kind: str) -> str:
    try:
        import sounddevice as sd
        info = sd.query_devices(selector or None, kind)
        return f"{info['name']} (#{info.get('index', '?')})"
    except Exception as exc:  # noqa: BLE001
        return f"unavailable ({exc})"


def collect(engine: Any, state: Any = None, *, query_devices: bool = True) -> VoiceDiagnosticsSnapshot:
    """Build a snapshot from a (possibly half-initialized) engine + reducer.

    query_devices=False skips the sounddevice lookups -- they can be slow
    enough to notice inside a request handler, and a caller that only wants
    the live counters shouldn't pay for them.
    """
    snap = VoiceDiagnosticsSnapshot()

    audio_io = getattr(engine, "_audio_io", None)
    settings = getattr(audio_io, "_settings", None)

    if query_devices:
        snap.input_device = _device_name(getattr(settings, "audio_input_device", None), "input")
        snap.output_device = _device_name(getattr(settings, "audio_output_device", None), "output")
    else:
        snap.input_device = snap.output_device = "not queried"

    snap.sample_rate = getattr(audio_io, "_sample_rate", None)

    # The device Whisper ACTUALLY loaded onto, not the one that was
    # requested -- the owner's own incident was CUDA silently falling back
    # to CPU with no visible sign short of reading logs.
    stt = getattr(getattr(engine, "_models", None), "stt", None)
    snap.stt_device = getattr(stt, "_device", None) or "not loaded"

    snap.mic_rms = float(_safe(lambda: audio_io.mic_level(), 0.0) or 0.0)
    snap.queue_depth = _safe(lambda: audio_io.queue_depth())
    # WavAudioIO/RemoteWsAudioIO don't have every counter DuplexAudioIO does;
    # a missing one stays None (unknown) rather than being reported as 0 (fine).
    snap.input_status_count = getattr(audio_io, "input_status_count", None)
    snap.input_overflow_count = getattr(audio_io, "input_overflow_count", None)
    snap.output_underrun_count = getattr(audio_io, "underrun_count", None)

    last = getattr(engine, "last_turn_metrics", None)
    if isinstance(last, dict):
        snap.last_vad_prob_max = last.get("vad_prob_max")
        snap.last_vad_prob_mean = last.get("vad_prob_mean")
        snap.last_turn_end_reason = last.get("turn_end_reason")
        snap.last_captured_audio_s = last.get("captured_audio_duration_s")
        snap.last_stt_s = last.get("stt_s")

    if state is not None:
        snap.capture = getattr(state, "capture", None)
        snap.response = getattr(state, "response", None)
        snap.display = _safe(lambda: state.display)
        snap.hud_state = _safe(lambda: state.hud_state)

    return snap


# ── Live-session registry ───────────────────────────────────────────────────

_current: "tuple[Any, Any] | None" = None


def register_session(engine: Any, state: Any = None) -> Any:
    """Record the engine/reducer a request handler should report on, and
    return whatever was registered before.

    The return value exists for the remote /ws audio session, which takes
    over reporting for the duration of a call and must hand it back to the
    local wakeword/PTT loop afterwards -- that loop registers once at
    startup and goes on running (merely paused) underneath, so clearing
    instead of restoring would leave GET /voice/status claiming no session
    exists while one very much does. Pass the returned token straight back
    to restore_session()."""
    global _current
    previous = _current
    _current = (engine, state)
    return previous


def restore_session(previous: Any) -> None:
    """Undo one register_session(), reinstating what it displaced."""
    global _current
    _current = previous


def clear_session() -> None:
    global _current
    _current = None


def current_snapshot(*, query_devices: bool = True) -> VoiceDiagnosticsSnapshot:
    """Snapshot of the running voice session, or an inert one (session_active
    False) when nothing is running -- deliberately not an error: "voice isn't
    running" is itself the diagnostic answer in that case."""
    if _current is None:
        return VoiceDiagnosticsSnapshot(notes=["no voice session is running in this process"])
    engine, state = _current
    snap = collect(engine, state, query_devices=query_devices)
    snap.session_active = True
    return snap
