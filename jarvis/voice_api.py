"""Voice I/O loop for API mode — runs as an asyncio Task inside the FastAPI event loop.

Usage (from api.py lifespan):
    from jarvis.voice_api import start_voice_task, trigger_ptt
    start_voice_task(agent, settings, wakeword=True)

PTT endpoint calls trigger_ptt() which signals the loop to skip wakeword and
listen immediately.
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


async def _voice_loop(agent, settings, wakeword: bool) -> None:
    global _ww_stop

    try:
        from jarvis.voice import VoiceEngine
    except ImportError as exc:
        logger.warning("[voice] Dependencies unavailable (%s) — voice disabled.", exc)
        return

    engine  = VoiceEngine(settings)
    loop    = asyncio.get_event_loop()
    _ww_stop = threading.Event()

    logger.info("[voice] Loading Whisper model…")
    try:
        await loop.run_in_executor(None, engine.load)
    except Exception as exc:
        logger.error("[voice] Whisper load failed: %s", exc)
        return
    logger.info("[voice] Whisper ready.")

    if wakeword:
        try:
            ok = await loop.run_in_executor(None, engine.load_wakeword)
        except Exception:
            ok = False
        if ok:
            logger.info('[voice] Wake-word ready. Listening for "Hey JARVIS"…')
        else:
            logger.warning("[voice] Wake-word model unavailable — PTT-only mode.")
            wakeword = False

    event_bus.state("idle")

    while True:
        try:
            # ── Wait for activation: wakeword OR PTT ────────────────────────
            if wakeword:
                _ww_stop.clear()
                ww_future = loop.run_in_executor(
                    None, engine.listen_for_wakeword, 0.5, _ww_stop
                )
                ptt_task = asyncio.create_task(_ptt_event.wait()) if _ptt_event else None

                if ptt_task:
                    done, pending = await asyncio.wait(
                        {asyncio.ensure_future(ww_future), ptt_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    _ww_stop.set()          # tell wakeword thread to exit within ~80 ms
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

            else:
                # PTT-only mode: wait for spacebar signal
                if _ptt_event:
                    event_bus.state("idle")
                    await _ptt_event.wait()
                    _ptt_event.clear()
                else:
                    await asyncio.sleep(0.5)
                    continue

            # ── Listen ───────────────────────────────────────────────────────
            event_bus.state("listening")
            text, lang = await loop.run_in_executor(None, engine.listen)

            if not text.strip():
                event_bus.state("idle")
                continue

            event_bus.message("u", text)
            event_bus.state("thinking")

            # ── Think ────────────────────────────────────────────────────────
            chunks: list[str] = []
            try:
                async for token in agent.chat_stream(text, detected_language=lang):
                    chunks.append(token)
            except Exception as exc:
                logger.error("[voice] chat_stream error: %s", exc)
                event_bus.state("idle")
                continue

            full_text = "".join(chunks)
            if not full_text.strip():
                event_bus.state("idle")
                continue

            event_bus.message("j", full_text)

            # ── Speak ────────────────────────────────────────────────────────
            event_bus.state("speaking")
            try:
                await engine.speak(full_text, lang)
            except Exception as exc:
                logger.error("[voice] TTS error: %s", exc)
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
