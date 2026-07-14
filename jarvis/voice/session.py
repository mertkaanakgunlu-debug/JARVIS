"""Shared conversation-turn orchestration for both the --voice CLI loop
(jarvis/cli.py) and the API/wakeword/PTT loop (jarvis/voice_api.py).

The subtle part worth implementing once: engine.events() must keep being
consumed *while* a turn's response (agent.chat_stream() -> engine.speak_stream())
is in flight, so a later BargeIn event can cancel it. Simply doing
`async for event in engine.events(): ... await respond(...)` would block the
event loop's consumption on awaiting the response, so BargeIn could never
arrive in time. This races the next event against the in-flight response task
via asyncio.wait(..., return_when=FIRST_COMPLETED) -- the same idiom
jarvis/voice_api.py already used pre-Faz-3 for racing wakeword against PTT.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from jarvis.voice.engine import RealtimeVoiceEngine
from jarvis.voice.events import BargeIn, FinalTranscript, MicLevel, SpeechStarted

logger = logging.getLogger(__name__)


# ── Confirmation gate over voice (Faz 4 / BUG-4) ─────────────────────────────
#
# agent.chat_stream() yields a single complete delta -- json.dumps({"__jarvis_
# confirm__": True, "id": conf_id, "payload": {...}}) -- when the graph
# interrupts for confirmation, instead of raising (that's chat()'s contract,
# not chat_stream()'s). Before Faz 4 nothing looked for this marker in any
# voice loop, so it was spoken to the user as raw JSON. The fix lives here,
# not per-loop: cli.py's --voice loop and voice_api.py's run_one_response
# (itself shared by the local wakeword/PTT loop AND api.py's remote /ws
# session) all drive a turn the same way, so the marker-detect / ask /
# resume-on-next-transcript logic is written once and reused by all three.

_AFFIRMATIVE_WORDS = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "correct", "confirm", "confirmed",
    "go ahead", "do it", "affirmative",
    "evet", "onaylıyorum", "onayla", "onaylıyorum", "tamam", "olur", "yap", "aynen",
})


@dataclass
class PendingConfirmation:
    conf_id: str
    payload: dict


def parse_confirm_marker(delta: str) -> dict | None:
    """If delta is exactly the __jarvis_confirm__ JSON marker chat_stream()
    yields on interrupt, return the parsed {"id", "payload", ...} dict; else
    None. chat_stream() always yields this marker as a single complete
    delta (one `yield json.dumps(...)` call) so no cross-chunk buffering
    is needed here."""
    s = delta.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict) and obj.get("__jarvis_confirm__") and obj.get("id"):
        return obj
    return None


def describe_confirmation(marker: dict, lang: str = "en") -> str:
    """Natural-language, TTS-safe question for a pending confirmation marker
    (as returned by parse_confirm_marker) -- no markdown, spoken aloud."""
    tools = (marker.get("payload") or {}).get("tools", [])
    parts = [t.get("description") or t.get("name") or "an action" for t in tools]
    if lang == "tr":
        body = " ve ".join(parts) if len(parts) > 1 else (parts[0] if parts else "hassas bir işlem")
        return f"Devam etmeden önce onayınız gerekiyor: {body}. Onaylıyor musunuz?"
    body = "; and ".join(parts) if len(parts) > 1 else (parts[0] if parts else "a sensitive action")
    return f"Before I continue, I need your OK to {body}. Should I go ahead?"


def is_affirmative(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(t == w or t.startswith(w + " ") or t.startswith(w + ",") for w in _AFFIRMATIVE_WORDS)


async def resolve_confirmation(
    agent: Any,
    engine: RealtimeVoiceEngine,
    pending: PendingConfirmation,
    transcript: str,
    lang: str,
    *,
    on_message: Callable[[str], None] | None = None,
) -> None:
    """Resume the turn agent.resume_and_stream() left interrupted, using
    `transcript` (the user's reply to describe_confirmation's question) as
    the approve/deny decision. Anything not recognized as affirmative denies
    -- fail-safe, same default as the CLI text prompt's default="n". The
    user's own words become the denial guidance the LLM sees, so "no, send
    it to Alice instead" still carries useful correction, not just a bare no.
    """
    decision = "approve" if is_affirmative(transcript) else f"deny:{transcript}"

    chunks: list[str] = []

    async def _collecting():
        async for token in agent.resume_and_stream(pending.conf_id, decision):
            chunks.append(token)
            yield token

    await engine.speak_stream(_collecting(), lang=lang)
    if on_message and chunks:
        on_message("".join(chunks))


async def _dispose_task(task: "asyncio.Task | None") -> None:
    """Cancel (if still running) and await a task so any exception it holds
    -- including a plain StopAsyncIteration from an exhausted async generator,
    which is a normal outcome here, not an error -- is retrieved rather than
    triggering asyncio's "exception was never retrieved" warning at GC time."""
    if task is None:
        return
    if not task.done():
        task.cancel()
    try:
        await task
    except BaseException:
        pass


# Sentinel: on_transcript returns this to end the session immediately (the
# caller has already spoken any farewell/confirmation itself).
STOP_SESSION = object()

OnTranscript = Callable[[str, str], Awaitable[Coroutine[Any, Any, None] | None | object]]


async def drive_voice_session(
    engine: RealtimeVoiceEngine,
    on_transcript: OnTranscript,
    *,
    on_speech_started: Callable[[], None] | None = None,
    on_barge_in: Callable[[], None] | None = None,
    on_mic_level: Callable[[MicLevel], None] | None = None,
    stop_after_first_turn: bool = False,
) -> str:
    """Consumes engine.events() and drives turns.

    on_transcript(text, lang) is awaited for each FinalTranscript with non-blank
    text. Its return value controls what happens next:
      - STOP_SESSION       -> end the session now, return "exit"
      - None               -> nothing to track (e.g. it already spoke a quick
                               synchronous confirmation itself) -- keep going
      - a coroutine object -> wrapped in a cancellable task tracked as "the
                               current turn"; a later BargeIn cancels it

    stop_after_first_turn: return "turn_complete" as soon as one turn's tracked
    coroutine finishes naturally (not via barge-in) -- used by wakeword mode,
    where the caller wants to re-gate on the wake phrase between turns rather
    than keep listening indefinitely.

    Returns "exit" | "turn_complete" | "ended" (engine.events() itself finished,
    e.g. because something outside this call closed the engine).
    """
    events_iter = engine.events().__aiter__()
    next_event_task: asyncio.Task = asyncio.ensure_future(events_iter.__anext__())
    turn_task: asyncio.Task | None = None

    try:
        while True:
            waiting = {next_event_task}
            if turn_task is not None:
                waiting.add(turn_task)

            done, _pending = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)

            if turn_task is not None and turn_task in done:
                if not turn_task.cancelled():
                    exc = turn_task.exception()
                    if exc is not None:
                        logger.error("[voice] turn task error: %s", exc, exc_info=exc)
                turn_task = None
                if stop_after_first_turn:
                    return "turn_complete"

            if next_event_task in done:
                try:
                    event = next_event_task.result()
                except StopAsyncIteration:
                    return "ended"
                next_event_task = asyncio.ensure_future(events_iter.__anext__())

                if isinstance(event, FinalTranscript):
                    if not event.text.strip():
                        continue
                    result = await on_transcript(event.text, event.lang)
                    if result is STOP_SESSION:
                        return "exit"
                    if result is not None:
                        if turn_task is not None and not turn_task.done():
                            turn_task.cancel()
                        turn_task = asyncio.ensure_future(result)
                elif isinstance(event, SpeechStarted):
                    if on_speech_started:
                        on_speech_started()
                elif isinstance(event, BargeIn):
                    if turn_task is not None and not turn_task.done():
                        turn_task.cancel()
                        try:
                            await turn_task
                        except asyncio.CancelledError:
                            pass
                        turn_task = None
                        if on_barge_in:
                            on_barge_in()
                elif isinstance(event, MicLevel):
                    if on_mic_level:
                        on_mic_level(event)
    finally:
        await _dispose_task(turn_task)
        await _dispose_task(next_event_task)
