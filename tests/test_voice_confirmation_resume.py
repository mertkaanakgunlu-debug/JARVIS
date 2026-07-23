"""jarvis/voice/session.py -- resolve_confirmation must not speak a raw
__jarvis_confirm__ marker aloud (review remediation).

Before this fix, resume_and_stream() yielding a fresh marker (a second
confirmable tool call in the resumed turn) was fed straight into TTS, so
the literal JSON -- tool name and args included -- was spoken aloud
instead of a new spoken confirmation question, and no PendingConfirmation
was re-armed for the next transcript to resolve it.
"""
from __future__ import annotations

import json

import pytest

from jarvis.voice.session import PendingConfirmation, resolve_confirmation


class _FakeAgent:
    def __init__(self, streams):
        self._streams = list(streams)
        self.calls: list[tuple[str, str]] = []

    async def resume_and_stream(self, conf_id, decision):
        self.calls.append((conf_id, decision))
        for token in self._streams.pop(0):
            yield token


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
