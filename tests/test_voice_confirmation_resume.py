"""jarvis/voice/session.py -- resolve_confirmation must not speak a raw
__jarvis_confirm__ marker aloud (review remediation).

Before this fix, resume_and_stream() yielding a fresh marker (a second
confirmable tool call in the resumed turn) was fed straight into TTS, so
the literal JSON -- tool name and args included -- was spoken aloud
instead of a new spoken confirmation question, and no PendingConfirmation
was re-armed for the next transcript to resolve it.

Also covers is_confirmation_still_pending() (external-review finding,
2026-07-23): both voice loops' PendingConfirmation flag is per-transport
local state with no way to learn the same conf_id was already resolved
through a different transport (the Electron HUD's /chat/confirm, since
this same session) or TTL-evicted by JarvisAgent's own sweep -- without
this guard the caller would silently consume the user's next, unrelated
utterance as a stale yes/no answer.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from jarvis.voice.session import (
    PendingConfirmation, is_affirmative, is_confirmation_still_pending, resolve_confirmation,
)


class _FakeAgent:
    def __init__(self, streams, pending_ids=("conf-1",)):
        self._streams = list(streams)
        self.calls: list[tuple[str, str]] = []
        self._pending = set(pending_ids)

    async def resume_and_stream(self, conf_id, decision, *, pre_claimed=None):
        self.calls.append((conf_id, decision))
        for token in self._streams.pop(0):
            yield token

    def has_pending_confirmation(self, conf_id):
        return conf_id in self._pending

    def claim_pending_confirmation(self, conf_id):
        if conf_id not in self._pending:
            return None
        self._pending.discard(conf_id)
        return {"claimed": conf_id}


class _FakeEngine:
    def __init__(self):
        self.spoken: list[list[str]] = []

    async def speak_stream(self, text_iter, lang="en"):
        chunks = [t async for t in text_iter]
        self.spoken.append(chunks)


def _marker(conf_id: str, tool: str) -> str:
    return json.dumps({
        "__jarvis_confirm__": True, "id": conf_id,
        "payload": {"tools": [{"name": tool, "description": f"do {tool}"}]},
    })


@pytest.mark.asyncio
async def test_second_interrupt_is_spoken_as_a_question_not_raw_json():
    agent = _FakeAgent([[_marker("conf-2", "google_calendar")]])
    engine = _FakeEngine()
    messages: list[str] = []
    pending_slot = [None]

    await resolve_confirmation(
        agent, engine, PendingConfirmation("conf-1", {"tools": []}), "yes", "en",
        on_message=messages.append,
        set_pending_confirmation=lambda p: pending_slot.__setitem__(0, p),
    )

    assert agent.calls == [("conf-1", "approve")]
    # the raw marker must never reach TTS or on_message as literal JSON
    assert not any("__jarvis_confirm__" in "".join(c) for c in engine.spoken)
    assert not any("__jarvis_confirm__" in m for m in messages)
    # a fresh PendingConfirmation for the SECOND interrupt was armed
    assert pending_slot[0] is not None
    assert pending_slot[0].conf_id == "conf-2"


@pytest.mark.asyncio
async def test_no_second_interrupt_speaks_response_normally():
    agent = _FakeAgent([["Email sent."]])
    engine = _FakeEngine()
    messages: list[str] = []

    await resolve_confirmation(
        agent, engine, PendingConfirmation("conf-1", {"tools": []}), "yes", "en",
        on_message=messages.append,
        set_pending_confirmation=lambda p: None,
    )

    assert messages == ["Email sent."]


@pytest.mark.asyncio
async def test_non_affirmative_transcript_denies_with_the_transcript_as_reason():
    agent = _FakeAgent([["Not sent."]])
    engine = _FakeEngine()

    await resolve_confirmation(
        agent, engine, PendingConfirmation("conf-1", {"tools": []}), "no, cancel that", "en",
    )

    assert agent.calls == [("conf-1", "deny:no, cancel that")]


@pytest.mark.asyncio
async def test_affirmative_with_a_trailing_period_approves():
    """Confirmed live 2026-07-24: STT emitted "Evet." (with a period), which
    is_affirmative missed, so the audit log recorded user_denied reason "Evet.".
    The resolve path must now approve it."""
    agent = _FakeAgent([["Event created."]])
    engine = _FakeEngine()

    await resolve_confirmation(
        agent, engine, PendingConfirmation("conf-1", {"tools": []}), "Evet.", "tr",
    )

    assert agent.calls == [("conf-1", "approve")]


# ── is_affirmative: trailing-punctuation normalization (live-caught) ──────────

@pytest.mark.parametrize("text", [
    "Evet.", "Evet", "evet,", "evet, lütfen", "Tamam.", "olur.",
    "Onaylıyorum.", "yap!", "aynen...", "yes.", "sure,",
])
def test_affirmative_variants(text):
    assert is_affirmative(text) is True


@pytest.mark.parametrize("text", [
    "Hayır.", "hayır", "iptal et", "", "evetsizlik", "no",
])
def test_non_affirmative_variants(text):
    assert is_affirmative(text) is False


# ── is_confirmation_still_pending (external-review finding, 2026-07-23) ─────

def test_still_pending_when_the_agent_still_holds_the_conf_id():
    agent = _FakeAgent([], pending_ids=("conf-1",))
    assert is_confirmation_still_pending(agent, PendingConfirmation("conf-1", {})) is True


def test_not_pending_once_resolved_through_another_transport():
    """The Electron HUD case: /chat/confirm/{id} already popped conf_id out
    of the agent's registry (approved or denied from the HUD) while the
    voice loop's local flag was still armed for the SAME id."""
    agent = _FakeAgent([], pending_ids=())  # nothing pending -- already resolved
    assert is_confirmation_still_pending(agent, PendingConfirmation("conf-1", {})) is False


def test_not_pending_once_ttl_evicted():
    """A different id is pending (a later confirmation), but NOT the stale
    one the voice loop is still holding -- the TTL-sweep-eviction case."""
    agent = _FakeAgent([], pending_ids=("conf-2",))
    assert is_confirmation_still_pending(agent, PendingConfirmation("conf-1", {})) is False


def test_a_real_jarvis_agent_reports_pending_state_honestly(isolated_cwd):
    """Same check, against the REAL JarvisAgent.has_pending_confirmation --
    not just the fake's mirror of it."""
    from jarvis.agent import JarvisAgent
    from jarvis.config import Settings

    agent = JarvisAgent.__new__(JarvisAgent)  # bypass heavy __init__; only this dict is needed
    agent._pending_confirmations = {}
    agent.settings = Settings(_env_file=None)

    assert agent.has_pending_confirmation("c1") is False
    agent._register_pending_confirmation("c1", {"configurable": {}}, recorder=None)
    assert agent.has_pending_confirmation("c1") is True
    agent._pending_confirmations.pop("c1")  # simulates resume_and_stream()'s own pop
    assert agent.has_pending_confirmation("c1") is False


# ── arm_and_speak_confirmation: arm BEFORE speak (barge-in race fix) ───────────
#
# Review remediation (2026-07-24): set_pending_confirmation() used to run AFTER
# `await engine.speak_stream(question)`, so a barge-in cancelling the turn task
# mid-question skipped the arm entirely and the user's next "evet" became a new
# turn instead of the confirmation's answer. The shared helper arms first.

@pytest.mark.asyncio
async def test_arm_happens_before_speak_so_a_cancellation_still_leaves_it_armed():
    """A CancelledError from the question TTS (a barge-in) must still leave the
    pending confirmation armed -- the arm ran before the await."""
    from jarvis.voice.session import arm_and_speak_confirmation

    pending_slot = [None]

    class _CancellingEngine:
        async def speak_stream(self, text_iter, lang="en"):
            raise asyncio.CancelledError()

    marker = {"id": "conf-9", "payload": {"tools": [{"name": "gmail", "description": "send"}]}}

    with pytest.raises(asyncio.CancelledError):
        await arm_and_speak_confirmation(
            _CancellingEngine(), marker, "tr",
            set_pending_confirmation=lambda p: pending_slot.__setitem__(0, p),
        )

    assert pending_slot[0] is not None
    assert pending_slot[0].conf_id == "conf-9"


@pytest.mark.asyncio
async def test_arm_and_speak_happy_path_arms_and_speaks_the_question():
    """Normal path: arms the next transcript AND speaks a natural question
    (never the raw marker JSON)."""
    from jarvis.voice.session import arm_and_speak_confirmation

    engine = _FakeEngine()
    pending_slot = [None]
    messages: list[str] = []

    marker = {"id": "conf-3", "payload": {"tools": [{"name": "google_calendar", "description": "create"}]}}

    await arm_and_speak_confirmation(
        engine, marker, "tr",
        set_pending_confirmation=lambda p: pending_slot.__setitem__(0, p),
        on_message=messages.append,
    )

    assert pending_slot[0] is not None and pending_slot[0].conf_id == "conf-3"
    assert engine.spoken, "the question must be spoken"
    assert not any("__jarvis_confirm__" in "".join(c) for c in engine.spoken)
    assert not any("__jarvis_confirm__" in m for m in messages)


@pytest.mark.asyncio
async def test_second_interrupt_reuses_the_shared_arm_helper(monkeypatch):
    """resolve_confirmation's second-interrupt re-arm must go through
    arm_and_speak_confirmation (so the ordering can't drift), and it must arm
    before speaking the second question."""
    import jarvis.voice.session as session

    calls: list[dict] = []
    real = session.arm_and_speak_confirmation

    async def _spy(engine, marker, lang, **kw):
        calls.append({"marker_id": marker["id"]})
        return await real(engine, marker, lang, **kw)

    monkeypatch.setattr(session, "arm_and_speak_confirmation", _spy)

    agent = _FakeAgent([[_marker("conf-2", "google_calendar")]])
    engine = _FakeEngine()
    pending_slot = [None]

    await session.resolve_confirmation(
        agent, engine, PendingConfirmation("conf-1", {"tools": []}), "yes", "tr",
        set_pending_confirmation=lambda p: pending_slot.__setitem__(0, p),
    )

    assert calls == [{"marker_id": "conf-2"}]
    assert pending_slot[0] is not None and pending_slot[0].conf_id == "conf-2"
