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
from jarvis.voice.events import BargeIn, FinalTranscript, MicLevel, SpeechStarted, TurnEnded
from jarvis.voice.state import VoiceState

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


def parse_final_marker(delta: str) -> str | None:
    """The authoritative answer text, if `delta` is the __jarvis_final__ marker.

    Post-MVP Faz 2.75 (Paket A). chat_stream yields this at the end of a turn
    when the graph's terminal response differs from the tokens it already
    streamed -- a critic revision or a verification repair. Tokens cannot be
    unsent, so consumers that CAN redraw (CLI, SSE clients) replace what they
    showed. Voice cannot: a sentence already spoken is already spoken, so the
    voice loop simply swallows this rather than reading JSON aloud.

    Same single-complete-delta guarantee as parse_confirm_marker -- one
    `yield json.dumps(...)` call, so no cross-chunk buffering.
    """
    s = delta.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict) and obj.get("__jarvis_final__"):
        return str(obj.get("text") or "")
    return None


def parse_progress_marker(delta: str) -> dict | None:
    """If delta is exactly the __jarvis_progress__ JSON marker chat_stream()/
    resume_and_stream() yield immediately before a contracted turn's expensive
    graph execution, return the parsed {"phase", "kind"?} dict; else None.

    Completion-contract TTFB. Emitted at most once per turn, always as a
    single complete delta (one `yield json.dumps(...)` call, exactly like the
    confirm/final markers above), so no cross-chunk buffering is needed here.
    Never carries a source path, filename, tool args or model prose -- see
    JarvisAgent's own marker-construction site for what it is allowed to hold.
    """
    s = delta.strip()
    if not (s.startswith("{") and s.endswith("}")):
        return None
    try:
        obj = json.loads(s)
    except (ValueError, TypeError):
        return None
    if isinstance(obj, dict) and obj.get("__jarvis_progress__") and obj.get("phase"):
        return obj
    return None


def describe_progress(marker: dict, lang: str = "en") -> str:
    """Natural-language, TTS-safe acknowledgement for a progress marker (as
    returned by parse_progress_marker) -- never the raw phase/kind string, and
    never phrased as a completed action: the tool this turn is buffering for
    may not have run yet (may not even succeed) when this is spoken."""
    kind = (marker or {}).get("kind")
    phase = (marker or {}).get("phase")
    if phase == "finalizing_action_result":
        if lang == "tr":
            return "İşlemin sonucunu kesinleştiriyorum."
        return "I'm finalizing the action's result."
    if lang == "tr":
        if kind == "chart":
            return "Grafiği hazırlıyorum."
        return "İstenen çıktıyı hazırlıyorum."
    return "I'm preparing the requested output."


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
    # Strip trailing sentence punctuation before matching (same class of fix as
    # text._normalize / the exit-phrase regex): STT emits "Evet." with a period,
    # which failed the exact "evet" match and got routed as a DENY -- confirmed
    # live 2026-07-24 (audit log: user_denied reason "Evet."). Only trailing
    # punctuation is stripped, so a mid-word comma variant ("evet, lütfen") still
    # matches the startswith(w + ",") branch below.
    t = (text or "").strip().lower().rstrip(".,!?…")
    if not t:
        return False
    return any(t == w or t.startswith(w + " ") or t.startswith(w + ",") for w in _AFFIRMATIVE_WORDS)


def is_confirmation_still_pending(agent: Any, pending: PendingConfirmation) -> bool:
    """Guard both voice loops' "next transcript = this confirmation's
    answer" routing decision against a STALE PendingConfirmation.

    External-review finding (2026-07-23): this flag is per-transport local
    state (see the module docstring above) with no way to learn that the
    same conf_id was already resolved elsewhere -- the Electron HUD's
    /chat/confirm round-trip (since this same session) or another client,
    or evicted by JarvisAgent._register_pending_confirmation's own TTL
    sweep. Call this BEFORE routing a transcript into resolve_confirmation()
    and fall through to a normal new turn when it returns False, so the
    user's actual next utterance is never silently swallowed as a
    yes/no answer to something already settled."""
    return agent.has_pending_confirmation(pending.conf_id)


async def arm_and_speak_confirmation(
    engine: RealtimeVoiceEngine,
    marker: dict,
    lang: str,
    *,
    set_pending_confirmation: Callable[["PendingConfirmation | None"], None] | None = None,
    on_message: Callable[[str], None] | None = None,
    state: VoiceState | None = None,
) -> None:
    """Arm the next transcript to resolve THIS confirmation, THEN speak the
    question. The ordering is the entire point.

    state (Faz D): set to "speaking" for the question's TTS, then
    "awaiting_confirmation" once it's been spoken -- the caller's next
    FinalTranscript is expected to be the user's yes/no answer, and the
    state should say so until it arrives.

    Review remediation (2026-07-24): each voice loop runs a turn as a
    cancellable task (drive_voice_session). Previously set_pending_confirmation()
    was called AFTER `await engine.speak_stream(question)`, so a barge-in that
    cancelled the task while the question was still being spoken skipped the arm
    entirely -- the user's next utterance, even a prompt "evet", was then treated
    as a brand-new turn instead of the confirmation's answer. Arming BEFORE any
    callback or await means a mid-question cancellation still leaves the
    confirmation resolvable.

    Shared by all three voice confirmation sites (cli._run_voice_response,
    voice_api.run_one_response, and resolve_confirmation's second-interrupt
    re-arm below) so this ordering can never drift back apart in one copy.
    """
    pending = PendingConfirmation(marker["id"], marker["payload"])
    if set_pending_confirmation is not None:
        set_pending_confirmation(pending)  # BEFORE any callback / await
    logger.info("[confirm] armed conf_id=%s", marker.get("id"))

    question = describe_confirmation(marker, lang)
    if on_message is not None:
        on_message(question)

    async def _question_stream(t=question):
        yield t

    if state is not None:
        state.set_response("speaking")
    await engine.speak_stream(_question_stream(), lang=lang)
    if state is not None:
        state.set_response("awaiting_confirmation")


async def resolve_confirmation(
    agent: Any,
    engine: RealtimeVoiceEngine,
    pending: PendingConfirmation,
    transcript: str,
    lang: str,
    *,
    on_message: Callable[[str], None] | None = None,
    set_pending_confirmation: Callable[["PendingConfirmation | None"], None] | None = None,
    pre_claimed: dict | None = None,
    state: VoiceState | None = None,
) -> None:
    """Resume the turn agent.resume_and_stream() left interrupted, using
    `transcript` (the user's reply to describe_confirmation's question) as
    the approve/deny decision. Anything not recognized as affirmative denies
    -- fail-safe, same default as the CLI text prompt's default="n". The
    user's own words become the denial guidance the LLM sees, so "no, send
    it to Alice instead" still carries useful correction, not just a bare no.

    Review remediation: resume_and_stream() can itself yield a SECOND
    __jarvis_confirm__ marker (a different confirmable tool call in the
    same resumed turn) -- before this fix, that marker was fed straight
    into engine.speak_stream() like ordinary text, so TTS spoke the raw
    JSON (tool name/args included) aloud instead of asking a new question,
    and the graph was left interrupted with no way to ever resume it. This
    now mirrors run_one_response()'s exact marker-detect/describe/re-arm
    pattern: the marker is intercepted before it reaches TTS, spoken as a
    natural question instead, and set_pending_confirmation() re-arms the
    next transcript to resolve THIS new interrupt.

    pre_claimed: forwarded verbatim to resume_and_stream() -- the caller
    should have already called agent.claim_pending_confirmation(pending.
    conf_id) atomically (see that method's docstring for the cross-
    transport race this closes) and pass the result here rather than let
    this call look conf_id up again.
    """
    decision = "approve" if is_affirmative(transcript) else f"deny:{transcript}"

    chunks: list[str] = []
    confirm_marker: dict | None = None

    async def _collecting():
        nonlocal confirm_marker
        async for token in agent.resume_and_stream(pending.conf_id, decision, pre_claimed=pre_claimed):
            marker = parse_confirm_marker(token)
            if marker is not None:
                confirm_marker = marker
                return
            progress = parse_progress_marker(token)
            if progress is not None:
                yield describe_progress(progress, lang)
                continue
            # Completion-contract TTFB (2026-08-07): a resumed turn can also
            # be contracted+enforce, and the graph's correction marker was
            # falling through here unrecognized -- the same class of bug
            # arm_and_speak_confirmation already closes for __jarvis_confirm__,
            # just missed here for __jarvis_final__, so raw JSON (tool text
            # included) was read aloud verbatim. Swallowed, not spoken: a
            # sentence already spoken cannot be unsaid (see
            # parse_final_marker's own docstring on this exact tradeoff).
            if parse_final_marker(token) is not None:
                continue
            chunks.append(token)
            yield token

    if state is not None:
        state.set_response("speaking")
    await engine.speak_stream(_collecting(), lang=lang)

    if confirm_marker is not None:
        # Second interrupt in the resumed turn -- re-arm via the shared helper so
        # this next-transcript ownership is set BEFORE the question TTS, same as
        # the first-interrupt sites (arm_and_speak_confirmation's docstring).
        await arm_and_speak_confirmation(
            engine, confirm_marker, lang,
            set_pending_confirmation=set_pending_confirmation,
            on_message=on_message,
            state=state,
        )
        return

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
    state: VoiceState | None = None,
    stop_after_first_turn: bool = False,
) -> str:
    """Consumes engine.events() and drives turns.

    on_transcript(text, lang) is awaited for each FinalTranscript with non-blank
    text. Its return value controls what happens next:
      - STOP_SESSION       -> end the session now, return "exit"
      - None               -> the callback ALREADY finished this turn
                               synchronously (it awaited its own speak_stream
                               before returning, e.g. cli.py's model-switch
                               branch) -- there is no task to track, so the
                               turn is over the moment it returns
      - a coroutine object -> wrapped in a cancellable task tracked as "the
                               current turn"; a later BargeIn cancels it

    stop_after_first_turn: return "turn_complete" as soon as one turn finishes
    naturally (not via barge-in) -- used by wakeword and PTT mode, where the
    caller wants to re-gate on the wake phrase / the next Enter press between
    turns rather than keep listening indefinitely.

    Review remediation (2026-07-25): "one turn finishes" originally meant ONLY
    a tracked coroutine completing, so a callback that handled the utterance
    synchronously and returned None never satisfied stop_after_first_turn --
    the session kept running with the mic open and never returned to the
    caller's gate. Live-reachable in --ptt: "flash modeline geç" takes cli.py's
    _detect_model_switch branch, which speaks its own confirmation and returns
    None, so the press-Enter gate was never re-armed for the next utterance.
    A synchronous turn now completes the session the same as a tracked one.

    state (Faz D): the caller CONSTRUCTS and owns this jarvis.voice.state.
    VoiceState instance (with its own on_change callback already attached)
    and passes it in here -- this function only ever mutates its capture
    axis (LISTENING/SPEECH_DETECTED/TRANSCRIBING). The SAME instance must
    also be passed to arm_and_speak_confirmation()/resolve_confirmation()
    (above) and the caller's own turn-response function (cli.py's
    _run_voice_response) so the response axis (THINKING/AWAITING_
    CONFIRMATION/SPEAKING) is set by the ONE place that actually knows an
    agent turn or TTS call is happening -- this function has no visibility
    into either. None (the default) skips all state tracking.

    Returns "exit" | "turn_complete" | "ended" (engine.events() itself finished,
    e.g. because something outside this call closed the engine).
    """
    if state is not None:
        state.set_capture("listening")

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
                # A completed turn that left a confirmation armed must NOT be
                # reset to idle here -- awaiting_confirmation means the turn's
                # own code (arm_and_speak_confirmation) deliberately wants this
                # state to persist until the user's next utterance resolves it.
                if state is not None and state.response != "awaiting_confirmation":
                    state.set_response("idle")
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
                    if state is not None:
                        # Response BEFORE capture, deliberately. Both orders
                        # end at the same pair, but going capture-first passes
                        # through (listening, idle) for one notification --
                        # which collapses to a wire "listening" frame between
                        # the STT "thinking" and the turn's own "thinking",
                        # i.e. a visible one-frame flicker on the HUD orb the
                        # instant transcription completes. Response-first
                        # passes through (transcribing, thinking) instead,
                        # whose display still resolves to transcribing -- the
                        # same wire value as before, so nothing is emitted.
                        state.set_response("thinking")
                        state.set_capture("listening")  # capture done; back to baseline
                    result = await on_transcript(event.text, event.lang)
                    if result is STOP_SESSION:
                        return "exit"
                    if result is None:
                        # Synchronously-completed turn (see the docstring): the
                        # callback already spoke whatever it had to before
                        # returning, so apply the exact same end-of-turn
                        # handling a tracked turn_task gets above -- including
                        # the awaiting_confirmation carve-out, since a
                        # synchronous branch can arm a confirmation too.
                        #
                        # ...but only when nothing else is in flight. An
                        # EARLIER turn_task still running owns both the
                        # response axis (it is mid-speak_stream) and the
                        # session's completion; claiming "idle" on its behalf
                        # would report silence over audible TTS, and returning
                        # "turn_complete" would cancel it outright via the
                        # finally below. Leave both to that turn's own
                        # completion branch, which handles them correctly.
                        if turn_task is None:
                            if state is not None and state.response != "awaiting_confirmation":
                                state.set_response("idle")
                            if stop_after_first_turn:
                                return "turn_complete"
                    else:
                        if turn_task is not None and not turn_task.done():
                            turn_task.cancel()
                        turn_task = asyncio.ensure_future(result)
                elif isinstance(event, SpeechStarted):
                    if state is not None:
                        state.set_capture("speech_detected")
                    if on_speech_started:
                        on_speech_started()
                elif isinstance(event, TurnEnded):
                    if state is not None:
                        state.set_capture("transcribing")
                elif isinstance(event, BargeIn):
                    if turn_task is not None and not turn_task.done():
                        turn_task.cancel()
                        try:
                            await turn_task
                        except asyncio.CancelledError:
                            pass
                        turn_task = None
                        if state is not None:
                            state.set_response("idle")
                            state.set_capture("speech_detected")  # the interruption IS new speech
                        if on_barge_in:
                            on_barge_in()
                elif isinstance(event, MicLevel):
                    if on_mic_level:
                        on_mic_level(event)
    finally:
        await _dispose_task(turn_task)
        await _dispose_task(next_event_task)
        # Review remediation (2026-07-25): every caller calls engine.stop()
        # immediately after this returns, so leaving capture on "listening"
        # (set on entry, never cleared) meant the reducer claimed a mic that
        # is physically closed -- most visibly at the --ptt/--wakeword gate,
        # which sits between turns showing "listening" while it waits for
        # Enter / the wake phrase. The session is over here by definition;
        # both axes go back to baseline.
        #
        # awaiting_confirmation is the one response state that legitimately
        # OUTLIVES the session: wakeword/PTT mode returns "turn_complete"
        # between turns while a question is still unanswered, and the caller's
        # pending_confirmation survives across drive_voice_session() calls to
        # be resolved by the next utterance. Same carve-out as the two
        # end-of-turn resets above.
        if state is not None:
            state.set_capture("idle")
            if state.response != "awaiting_confirmation":
                state.set_response("idle")
