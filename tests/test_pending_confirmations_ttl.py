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
