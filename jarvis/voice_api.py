"""Voice I/O loop for API mode — runs as an asyncio Task inside the FastAPI event loop.

Usage (from api.py lifespan):
    from jarvis.voice_api import start_voice_task, trigger_ptt
    start_voice_task(agent, settings, wakeword=True)

PTT endpoint calls trigger_ptt() which signals the loop to skip wakeword and
listen immediately. Unlike the --voice CLI (which listens continuously with no
gate when --wakeword isn't passed), this loop always gates each turn on either
the wake phrase or a PTT press — an always-open, ungated mic by default would
be a much bigger default footprint for a background server process than for a
deliberately-invoked CLI session.
"""

from __future__ import annotations

import asyncio
import threading
import logging
from typing import Optional

from jarvis.ws import event_bus

logger = logging.getLogger(__name__)

# ── Module-level state ────────────────────────────────────────────────────────
_voice_task: Optional[asyncio.Task] = None
_ptt_event:  Optional[asyncio.Event]    = None   # asyncio — signals PTT press
_ww_stop:    Optional[threading.Event]  = None   # threading — stops wakeword thread
_local_paused: bool = False   # set by pause_local_voice()/resume_local_voice()


async def _wait_for_activation(ww_detector, wakeword: bool, loop: asyncio.AbstractEventLoop) -> bool:
    """Waits for wake-word OR PTT (or just PTT, if wakeword is disabled).
    Returns True once activated; only returns False if it should give up this
    cycle (no PTT event configured and wakeword disabled — polls again)."""
    global _ww_stop

    if wakeword and ww_detector is not None:
        _ww_stop.clear()
        ww_future = loop.run_in_executor(None, ww_detector.listen, 0.5, _ww_stop)
        ptt_task = asyncio.create_task(_ptt_event.wait()) if _ptt_event else None

        if ptt_task:
            done, pending = await asyncio.wait(
                {asyncio.ensure_future(ww_future), ptt_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            _ww_stop.set()  # tell the wakeword thread to exit within ~80ms
            for t in pending:
                t.cancel()
                try:
                    await t
                except (asyncio.CancelledError, Exception):
                    pass
            if _ptt_event and _ptt_event.is_set():
                _ptt_event.clear()
        else:
            await ww_future
        return True

    # PTT-only mode: wait for the spacebar/Electron signal
    if _ptt_event:
        event_bus.state("idle")
        await _ptt_event.wait()
        _ptt_event.clear()
        return True

    await asyncio.sleep(0.5)
    return False


async def run_one_response(agent, engine, text: str, lang: str) -> None:
    """One turn's response, as a cancellable task (see jarvis/voice/session.py) —
    a BargeIn event interrupts this mid-flight. Public (no leading underscore):
    reused as-is by jarvis/api.py's /ws remote-audio session handler, not just
    this module's local wakeword/PTT loop."""
    event_bus.message("u", text)
    event_bus.state("thinking")

    response_chunks: list[str] = []

    async def _collecting_stream():
        async for token in agent.chat_stream(text, detected_language=lang):
            response_chunks.append(token)
            yield token

    event_bus.state("speaking")
    try:
        await engine.speak_stream(_collecting_stream(), lang=lang)
    except Exception as exc:
        logger.error("[voice] turn error: %s", exc, exc_info=True)

    if response_chunks:
        event_bus.message("j", "".join(response_chunks))


async def _voice_loop(agent, settings, wakeword: bool) -> None:
    global _ww_stop

    try:
        from jarvis.voice.engine import RealtimeVoiceEngine, get_shared_voice_models
        from jarvis.voice.io_duplex import DuplexAudioIO
        from jarvis.voice.wakeword import WakewordDetector
        from jarvis.voice.session import drive_voice_session
    except ImportError as exc:
        logger.warning("[voice] Dependencies unavailable (%s) — voice disabled.", exc)
        return

    loop = asyncio.get_event_loop()
    _ww_stop = threading.Event()

    logger.info("[voice] Loading voice models…")
    try:
        # Shared with any remote-audio /ws session started later in this same
        # process (jarvis/api.py) — whichever loads first, the other reuses it.
        models = await get_shared_voice_models(settings)
        engine = RealtimeVoiceEngine(DuplexAudioIO(settings), settings, models=models)
        await engine.load()
    except Exception as exc:
        logger.error("[voice] Voice model load failed: %s", exc)
        return
    logger.info("[voice] Voice models ready.")

    ww_detector = None
    if wakeword:
        ww_detector = WakewordDetector()
        try:
            ok = await loop.run_in_executor(None, ww_detector.load)
        except Exception:
            ok = False
        if ok:
            logger.info('[voice] Wake-word ready. Listening for "Hey JARVIS"…')
        else:
            logger.warning("[voice] Wake-word model unavailable — PTT-only mode.")
            wakeword = False

    event_bus.state("idle")

    async def _handle_transcript(text: str, lang: str):
        # No exit-phrase handling here, unlike the CLI's --voice mode: this loop
        # is a persistent background task tied to the server's lifetime, not
        # the conversation's — saying "goodbye" is just a normal chat turn
        # (matches the pre-Faz-3 behavior, which never special-cased it either).
        return run_one_response(agent, engine, text, lang)

    def _on_barge_in() -> None:
        event_bus.state("listening")

    while True:
        try:
            if _local_paused:
                # A remote-audio session (Electron/phone) is active — see
                # pause_local_voice()/resume_local_voice(). Idle instead of
                # racing it for the shared conversation/agent state.
                await asyncio.sleep(0.5)
                continue

            activated = await _wait_for_activation(ww_detector, wakeword, loop)
            if not activated:
                continue

            from jarvis.voice.session_manager import LOCAL_OWNER, try_claim, release

            if not try_claim(LOCAL_OWNER):
                # Lost the race to a remote session that claimed it first —
                # back off briefly rather than spin.
                await asyncio.sleep(0.5)
                continue

            event_bus.state("listening")
            await engine.start()
            try:
                await drive_voice_session(
                    engine, _handle_transcript,
                    on_barge_in=_on_barge_in,
                    stop_after_first_turn=True,
                )
            finally:
                await engine.stop()
                release(LOCAL_OWNER)
            event_bus.state("idle")

        except asyncio.CancelledError:
            event_bus.state("idle")
            if _ww_stop:
                _ww_stop.set()
            raise
        except Exception as exc:
            logger.error("[voice] Unexpected error: %s", exc, exc_info=True)
            event_bus.state("idle")
            await asyncio.sleep(1)


def start_voice_task(agent, settings, wakeword: bool = False) -> None:
    """Schedule the voice loop as a FastAPI background task (call from lifespan)."""
    global _voice_task, _ptt_event
    _ptt_event = asyncio.Event()
    if _voice_task is None or _voice_task.done():
        _voice_task = asyncio.create_task(
            _voice_loop(agent, settings, wakeword),
            name="jarvis-voice",
        )
        logger.info("[voice] Voice task started (wakeword=%s).", wakeword)


def pause_local_voice() -> None:
    """Stop the local wakeword/PTT loop from claiming new turns (Faz 3) — called
    when a remote-audio session (Electron/phone over /ws) starts, since
    Electron's main process always spawns the backend with --wakeword
    (electron/src/main/index.js), which would otherwise compete with a remote
    session for the shared conversation/agent state. Any turn already in
    progress finishes normally; only the *next* activation is gated."""
    global _local_paused
    _local_paused = True


def resume_local_voice() -> None:
    """Re-enable the local wakeword/PTT loop — called when a remote-audio
    session ends (client-requested stop, or disconnect)."""
    global _local_paused
    _local_paused = False


def trigger_ptt() -> bool:
    """Signal the voice loop to activate immediately (skip wakeword wait).

    Called by the POST /voice/ptt/start endpoint when the user presses
    Alt+Space in the HUD. Returns False if voice is not running.
    """
    if _ptt_event is None:
        return False
    if _voice_task is not None and _voice_task.done():
        return False
    _ptt_event.set()
    return True
