"""jarvis/agent.py's JarvisAgent._register_pending_confirmation() -- review
remediation.

Before this, every confirmable turn inserted into self._pending_confirmations
with no eviction. No Electron/mobile UI resolves this prompt yet (CLAUDE.md's
safety-model section), and any SSE client that disconnects mid-stream never
calls resume, so an abandoned entry -- holding the turn's graph config, HUD
callback, and usage recorder -- leaked forever on a long-running --api
server. Eviction piggybacks on the next registration rather than a
background sweep loop.
"""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.agent import JarvisAgent
from jarvis.config import Settings


def _bare_agent():
    agent = JarvisAgent.__new__(JarvisAgent)
    agent.settings = Settings(_env_file=None, approval_ttl_sec=300)
    agent._pending_confirmations = {}
    return agent


def test_stale_entry_is_evicted_on_next_registration(monkeypatch):
    agent = _bare_agent()
    clock = [1000.0]
    monkeypatch.setattr("jarvis.agent.time.monotonic", lambda: clock[0])

    agent._register_pending_confirmation("old", {"thread_id": "t1"}, None)
    assert "old" in agent._pending_confirmations

    # Well past 2x approval_ttl_sec (300s) later.
    clock[0] = 1000.0 + 700.0
    agent._register_pending_confirmation("new", {"thread_id": "t2"}, None)

    assert "old" not in agent._pending_confirmations
    assert "new" in agent._pending_confirmations


def test_fresh_entry_is_not_evicted(monkeypatch):
    agent = _bare_agent()
    clock = [1000.0]
    monkeypatch.setattr("jarvis.agent.time.monotonic", lambda: clock[0])

    agent._register_pending_confirmation("a", {"thread_id": "t1"}, None)

    clock[0] = 1000.0 + 5.0  # well within the TTL
    agent._register_pending_confirmation("b", {"thread_id": "t2"}, None)

    assert set(agent._pending_confirmations) == {"a", "b"}


def test_registration_stores_config_and_recorder():
    agent = _bare_agent()
    recorder = SimpleNamespace(marker="rec")

    agent._register_pending_confirmation("c1", {"thread_id": "t1"}, recorder)

    entry = agent._pending_confirmations["c1"]
    assert entry["config"] == {"thread_id": "t1"}
    assert entry["recorder"] is recorder
    assert "created_at" in entry


# ── claim_pending_confirmation: atomic ownership (cross-transport race fix) ──
#
# Follow-up finding (2026-07-23): has_pending_confirmation()'s check-then-act
# pattern (check membership, THEN later call resume_and_stream() which pops)
# is not atomic against a second transport claiming the same conf_id in the
# gap between the two -- both voice loops route a transcript into
# resolve_confirmation() as an unawaited coroutine that the caller schedules
# via asyncio.ensure_future rather than running inline, leaving a real
# scheduling window. claim_pending_confirmation() is a single synchronous
# dict.pop() -- nothing else can interleave with it on this event loop -- so
# only one caller can ever successfully claim a given conf_id.

def test_claim_removes_and_returns_the_entry():
    agent = _bare_agent()
    agent._register_pending_confirmation("c1", {"thread_id": "t1"}, "rec")

    claimed = agent.claim_pending_confirmation("c1")

    assert claimed["config"] == {"thread_id": "t1"}
    assert claimed["recorder"] == "rec"
    assert "c1" not in agent._pending_confirmations


def test_claim_returns_none_when_never_registered():
    agent = _bare_agent()
    assert agent.claim_pending_confirmation("never-existed") is None


def test_second_claim_of_the_same_id_returns_none():
    """The exact cross-transport race this method exists to close: whichever
    caller claims first gets the entry; a second claim attempt (a different
    transport that raced it) gets nothing, never a stale/duplicate copy."""
    agent = _bare_agent()
    agent._register_pending_confirmation("c1", {"thread_id": "t1"}, "rec")

    first = agent.claim_pending_confirmation("c1")
    second = agent.claim_pending_confirmation("c1")

    assert first is not None
    assert second is None


def test_has_pending_confirmation_is_true_before_claim_false_after():
    agent = _bare_agent()
    agent._register_pending_confirmation("c1", {"thread_id": "t1"}, "rec")

    assert agent.has_pending_confirmation("c1") is True
    agent.claim_pending_confirmation("c1")
    assert agent.has_pending_confirmation("c1") is False
