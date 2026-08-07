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
_bg_watchers: set = set()     # strong refs to _watch_background_task's tasks (Faz 4)


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


def _set_response(state, value: str) -> None:
    """Move the response axis, whether or not this caller wired a reducer.

    Transport-parity review finding (2026-07-25): Faz D's VoiceState reached
    only cli.py, so this module and api.py's /ws session kept hand-picking
    event_bus.state() literals at each call site -- two transports, two
    different state contracts, and no way for the HUD to see the reducer's
    priority rules (a capture axis and a response axis being simultaneously
    active, awaiting_confirmation outranking routine progress). Both callers
    now pass a VoiceState; the event_bus fallback stays only so a state-less
    caller still produces wire frames instead of silently going dark.
    """
    if state is not None:
        state.set_response(value)
    else:
        event_bus.state(value)


async def run_one_response(
    agent, engine, text: str, lang: str,
    transport: str = "voice-local", set_pending_confirmation=None, state=None,
) -> None:
    """One turn's response, as a cancellable task (see jarvis/voice/session.py) —
    a BargeIn event interrupts this mid-flight. Public (no leading underscore):
    reused as-is by jarvis/api.py's /ws remote-audio session handler, not just
    this module's local wakeword/PTT loop.

    Faz 4 adds two things over the pre-Faz-4 version:
    (a) BUG-4: chat_stream() interrupting for confirmation yields the
        __jarvis_confirm__ marker as a single delta -- detected here and
        swapped for a natural spoken question instead of being read aloud
        as raw JSON (see jarvis/voice/session.py's parse_confirm_marker).
    (b) Async scheduler: a query TaskExecutor.should_async() flags (long
        tools -- deep_web_research/report_compile/geo_math/...) is handed
        off to the background executor with a short spoken acknowledgement
        instead of blocking this turn -- and the mic -- in silence for up
        to minutes. Only active when agent has a _task_executor attached
        (--api mode; see jarvis/api.py's _wire_routers()) -- the standalone
        CLI --voice loop has no such executor and is unaffected.
    """
    from jarvis.voice.session import (
        arm_and_speak_confirmation, describe_progress, parse_confirm_marker,
        parse_final_marker, parse_progress_marker,
    )

    event_bus.message("u", text)
    _set_response(state, "thinking")

    executor = getattr(agent, "_task_executor", None)
    if executor is not None and executor.should_async(text):
        ack = (
            "Bu biraz zaman alacak, arka planda üzerinde çalışıyorum. Bitince haber veririm."
            if lang == "tr" else
            "This will take a moment -- I'm working on it in the background and will let "
            "you know when it's done."
        )
        _set_response(state, "speaking")

        async def _ack_stream(t=ack):
            yield t

        try:
            await engine.speak_stream(_ack_stream(), lang=lang)
        except Exception as exc:
            logger.error("[voice] background-ack TTS error: %s", exc, exc_info=True)
        event_bus.message("j", ack)
        task = executor.submit(text)
        _watch_background_task(task)
        return

    response_chunks: list[str] = []
    confirm_marker: dict | None = None

    async def _collecting_stream():
        nonlocal confirm_marker
        async for token in agent.chat_stream(text, detected_language=lang, transport=transport):
            marker = parse_confirm_marker(token)
            if marker is not None:
                confirm_marker = marker
                return
            # Completion-contract TTFB: a short, deterministic acknowledgement
            # instead of the silence a contracted+enforce turn used to leave
            # until the whole graph finished. Spoken but kept OUT of
            # response_chunks, same reasoning as cli.py's _run_voice_response.
            progress = parse_progress_marker(token)
            if progress is not None:
                yield describe_progress(progress, lang)
                continue
            # Review remediation (completion-contract TTFB, 2026-08-07): this
            # loop had no __jarvis_final__ handling at all -- a critic
            # revision or verification-repair correction on an uncontracted
            # turn fell straight through to response_chunks/TTS as raw JSON.
            # Swallowed, not spoken, mirroring cli.py's _run_voice_response:
            # TTS has already said the superseded sentences and there is no
            # un-saying them.
            if parse_final_marker(token) is not None:
                return
            response_chunks.append(token)
            yield token

    _set_response(state, "speaking")
    try:
        await engine.speak_stream(_collecting_stream(), lang=lang)
    except Exception as exc:
        logger.error("[voice] turn error: %s", exc, exc_info=True)

    if confirm_marker is not None:
        # Arm-before-speak via the shared helper (see arm_and_speak_confirmation):
        # the next-transcript ownership is set before the question TTS, so a
        # barge-in cancelling this turn mid-question still leaves it armed.
        try:
            await arm_and_speak_confirmation(
                engine, confirm_marker, lang,
                set_pending_confirmation=set_pending_confirmation,
                on_message=lambda q: event_bus.message("j", q),
                state=state,
            )
        except Exception as exc:
            logger.error("[voice] confirmation prompt TTS error: %s", exc, exc_info=True)
        return

    if response_chunks:
        event_bus.message("j", "".join(response_chunks))


def _watch_background_task(task) -> None:
    """Fire a Windows toast when a voice-submitted background task finishes --
    closes the loop on the "I'm working on it" ack for the in-the-room case
    (TaskExecutor._dispatch_push already covers the away-from-PC/mobile case
    via FCM). Polls task.status rather than requiring TaskExecutor to grow a
    callback mechanism it doesn't otherwise need."""

    async def _watch() -> None:
        try:
            while task.status in ("queued", "running"):
                await asyncio.sleep(2)
        except asyncio.CancelledError:
            return
        try:
            from jarvis.notify import toast
            if task.status == "done":
                toast("JARVIS ✓ Görev tamamlandı", (task.result_text or "")[:120])
            elif task.status == "failed":
                toast("JARVIS ✗ Görev başarısız", (task.error or "Bilinmeyen hata")[:120])
        except Exception:
            pass

    t = asyncio.create_task(_watch())
    _bg_watchers.add(t)
    t.add_done_callback(_bg_watchers.discard)


async def _voice_loop(agent, settings, wakeword: bool) -> None:
    global _ww_stop

    try:
        from jarvis.voice.engine import RealtimeVoiceEngine, get_shared_voice_models
        from jarvis.voice.io_duplex import DuplexAudioIO
        from jarvis.voice.wakeword import WakewordDetector
        from jarvis.voice.session import drive_voice_session, resolve_confirmation
        from jarvis.voice.state import VoiceState, hud_state_emitter
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

    pending_confirmation = None

    # Transport parity (2026-07-25): ONE reducer per loop, exactly like
    # cli.py's --voice session. drive_voice_session() drives its capture
    # axis, run_one_response()/arm_and_speak_confirmation()/
    # resolve_confirmation() drive its response axis, and hud_state_emitter
    # turns each real change into a single {"type":"state"} frame -- so the
    # HUD sees the same state machine the CLI does instead of this module's
    # former hand-placed literals.
    voice_state = VoiceState(on_change=hud_state_emitter(event_bus.state))

    # Make this loop's engine/reducer readable by GET /voice/status.
    from jarvis.voice.diagnostics import register_session
    register_session(engine, voice_state)

    def _set_pending(p) -> None:
        nonlocal pending_confirmation
        pending_confirmation = p

    async def _handle_transcript(text: str, lang: str):
        # No exit-phrase handling here, unlike the CLI's --voice mode: this loop
        # is a persistent background task tied to the server's lifetime, not
        # the conversation's — saying "goodbye" is just a normal chat turn
        # (matches the pre-Faz-3 behavior, which never special-cased it either).
        nonlocal pending_confirmation

        # Faz 4 / BUG-4: a pending confirmation always consumes the *next*
        # utterance as its yes/no answer, not a new command -- UNLESS it was
        # already resolved through a different transport in the meantime
        # (the Electron HUD also listens for confirmation_required over WS
        # and can approve/deny it from /chat/confirm). Follow-up finding
        # (2026-07-23): the earlier is_confirmation_still_pending() pre-check
        # here was NOT atomic against that -- this coroutine is only
        # scheduled via asyncio.ensure_future by drive_voice_session, not run
        # inline, so a real gap exists between "check" and
        # resume_and_stream()'s own pop. claim_pending_confirmation() is a
        # single synchronous dict.pop() (nothing else can interleave on this
        # event loop) -- claim FIRST, proceed only if we actually got it.
        if pending_confirmation is not None:
            pending, pending_confirmation = pending_confirmation, None
            claimed = agent.claim_pending_confirmation(pending.conf_id)
            if claimed is not None:
                return resolve_confirmation(
                    agent, engine, pending, text, lang,
                    on_message=lambda full: event_bus.message("j", full),
                    set_pending_confirmation=_set_pending,
                    pre_claimed=claimed,
                    state=voice_state,
                )
            # Resolved elsewhere or TTL-evicted -- treat this utterance as a
            # brand-new turn, falling through below.

        return run_one_response(
            agent, engine, text, lang, transport="voice-local",
            set_pending_confirmation=_set_pending, state=voice_state,
        )

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

            await engine.start()
            try:
                # No hand-placed listening/idle frames around this any more:
                # drive_voice_session() sets capture on entry and back to
                # idle on exit, and voice_state's emitter turns each real
                # change into one wire frame (including the barge-in
                # transition the old _on_barge_in callback used to publish).
                await drive_voice_session(
                    engine, _handle_transcript,
                    state=voice_state,
                    stop_after_first_turn=True,
                )
            finally:
                await engine.stop()
                release(LOCAL_OWNER)

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
