"""Faz 7.3 (P1) -- /reset must never archive a DIFFERENT client's conversation.

The exact race the review named: client A works in conversation A; client
B sends a message in conversation B, switching the shared agent's active
session to B; client A calls /reset with no way to say WHICH conversation
it means -- so the old endpoint reset whatever was currently active (B's),
silently archiving a conversation A never touched and leaving B's client
mid-turn with an archived session.

Two layers, same precedent as test_reset_lifecycle.py (unbound-method
fakes for the agent-level unit tests) and test_workflow_approval_service.py
(real starlette TestClient, no-lifespan pattern for the endpoint tests).
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import jarvis.api as api
from jarvis.agent import JarvisAgent


class _FakeSessionStore:
    def __init__(self, *, turn_idx_by_session: dict[str, int] | None = None):
        self.archived: list[str] = []
        self._n = 0
        self._turn_idx = turn_idx_by_session or {}

    def archive_session(self, sid):
        self.archived.append(sid)

    def new_session(self):
        self._n += 1
        return f"new{self._n}"

    def last_turn_idx(self, sid):
        return self._turn_idx.get(sid, 0)

    def load_full_history(self, sid):
        return []

    def list_sessions(self, n):
        return []


def _fake_agent(*, active_session="B", active_history=None, turn_idx_by_session=None):
    agent = SimpleNamespace(
        _state_lock=threading.Lock(),
        session_id=active_session,
        _history=active_history if active_history is not None else ["b-msg"],
        _turn=5,
        session_store=_FakeSessionStore(turn_idx_by_session=turn_idx_by_session),
        _bg_tasks=set(),
        scheduled=[],
        _last_turn_trace={"provider": "ollama"},
        _last_turn_used_pro=True,
        _pending_confirmations={"conf-1": {}},
    )
    agent._reset_state_sync = lambda target=None: JarvisAgent._reset_state_sync(agent, target)
    agent._schedule_summarize_one = lambda sid: agent.scheduled.append(sid)
    return agent


# ── JarvisAgent.reset_conversation_async: the cross-client invariant ─────────

@pytest.mark.asyncio
async def test_resetting_a_non_active_conversation_never_touches_the_active_one():
    """The review's exact scenario: B is active (client B's in-flight
    conversation); client A resets conversation A."""
    agent = _fake_agent(active_session="B", active_history=["b-msg-1", "b-msg-2"])

    archived_id, new_id = await JarvisAgent.reset_conversation_async(agent, "A")

    assert archived_id == "A"
    assert agent.session_store.archived == ["A"]  # ONLY A -- never B
    # B's in-memory turn state is completely untouched.
    assert agent.session_id == "B"
    assert agent._history == ["b-msg-1", "b-msg-2"]
    assert agent._turn == 5
    assert agent._last_turn_trace == {"provider": "ollama"}
    assert agent._pending_confirmations == {"conf-1": {}}
    assert new_id != "B" and new_id not in ("A",)


@pytest.mark.asyncio
async def test_resetting_the_active_conversation_behaves_like_plain_reset():
    agent = _fake_agent(active_session="B", active_history=["b-msg"])

    archived_id, new_id = await JarvisAgent.reset_conversation_async(agent, "B")

    assert archived_id == "B"
    assert agent.session_store.archived == ["B"]
    assert agent.session_id == new_id  # the active pointer DID move
    assert agent._history == [] and agent._turn == 0


@pytest.mark.asyncio
async def test_empty_conversation_id_resets_whatever_is_active():
    agent = _fake_agent(active_session="B", active_history=["b-msg"])

    archived_id, new_id = await JarvisAgent.reset_conversation_async(agent, "")

    assert archived_id == "B"
    assert agent.session_id == new_id


@pytest.mark.asyncio
async def test_event_bus_notification_survives_a_concurrent_session_switch(monkeypatch):
    """Review remediation (TOCTOU): reset_conversation_async used to decide
    whether to fire event_bus.session(...) by comparing self.session_id
    AFTER _reset_state_sync's lock was already released -- a concurrent
    request that switched the active session in that exact gap made the
    comparison see a since-changed value, silently dropping the
    notification for a reset that legitimately targeted the active
    conversation. was_active is now decided ATOMICALLY inside
    _reset_state_sync's own lock, so the notification must still fire
    (with the correct new_session_id) even if self.session_id has since
    moved on to some third value by the time this check runs."""
    import jarvis.agent as agent_mod

    class _FakeEventBus:
        def __init__(self):
            self.calls: list[tuple[str, object]] = []
            self.model_statuses: list[object] = []

        def session(self, session_id, topic):
            self.calls.append((session_id, topic))

        def model_status(self, trace):
            # Kept OUT of `calls`, which this test asserts by exact equality:
            # the subject here is which session id the notification carries,
            # and an unrelated event appearing in that list would make the
            # assertion fail for a reason it is not about.
            #
            # Recorded separately rather than dropped, because the emission is
            # real and worth seeing: _reset_state_sync clears the HUD's
            # provider/model readout, so a reset cannot leave the ARCHIVED
            # session's model displayed as if it were current.
            self.model_statuses.append(trace)

    fake_bus = _FakeEventBus()
    monkeypatch.setattr(agent_mod, "event_bus", fake_bus)

    agent = _fake_agent(active_session="B", active_history=["b-msg"])
    real_reset_state_sync = agent._reset_state_sync

    def _reset_then_race(target=None):
        result = real_reset_state_sync(target)
        # Simulate another coroutine's chat_stream(conversation_id="C")
        # switching the shared agent's active pointer in the gap between
        # this method's lock release and reset_conversation_async's
        # post-await code running.
        agent.session_id = "C-from-concurrent-request"
        return result

    agent._reset_state_sync = _reset_then_race

    archived_id, new_id = await JarvisAgent.reset_conversation_async(agent, "")  # resets active (B)

    assert archived_id == "B"
    assert fake_bus.calls == [(new_id, None)]  # fired with the REAL new id, not the raced value
    # The reset also clears the HUD's model readout — otherwise the archived
    # session's provider/model stays on screen labelled as the live one.
    assert fake_bus.model_statuses == [None]


@pytest.mark.asyncio
async def test_summary_is_scheduled_for_the_archived_non_active_session():
    agent = _fake_agent(active_session="B", turn_idx_by_session={"A": 3})

    await JarvisAgent.reset_conversation_async(agent, "A")

    assert agent.scheduled == ["A"]


@pytest.mark.asyncio
async def test_no_summary_scheduled_for_an_empty_non_active_session():
    agent = _fake_agent(active_session="B", turn_idx_by_session={})  # A never had a turn

    await JarvisAgent.reset_conversation_async(agent, "A")

    assert agent.scheduled == []


# ── API endpoint: the full cross-client scenario over real HTTP ──────────────

class _StubAgent:
    """Enough of JarvisAgent's surface for POST /reset -- delegates to the
    REAL reset_conversation_async (unbound-method style, same precedent as
    _fake_agent above) so the endpoint test exercises real agent logic,
    not a re-description of it."""

    def __init__(self):
        self._state_lock = threading.Lock()
        self.session_id = "B"
        self._history = ["b-msg"]
        self._turn = 5
        self.session_store = _FakeSessionStore(turn_idx_by_session={"A": 2})
        self._bg_tasks = set()
        self._last_turn_trace = None
        self._last_turn_used_pro = None
        self._pending_confirmations = {}

    def _schedule_summarize_one(self, sid):
        pass

    def _reset_state_sync(self, target_session_id=None):
        return JarvisAgent._reset_state_sync(self, target_session_id)

    async def reset_conversation_async(self, conversation_id=""):
        return await JarvisAgent.reset_conversation_async(self, conversation_id)


def _client(monkeypatch, agent) -> TestClient:
    monkeypatch.setattr(api, "_agent", agent)
    monkeypatch.setattr(api, "_settings", None)
    return TestClient(api.app)


def test_reset_endpoint_with_conversation_id_does_not_disturb_the_active_session(
    isolated_cwd, monkeypatch
):
    agent = _StubAgent()
    client = _client(monkeypatch, agent)

    resp = client.post("/reset", json={"conversation_id": "A"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["archived_conversation_id"] == "A"
    assert body["session_id"] != "B" and body["session_id"] != "A"
    assert agent.session_store.archived == ["A"]
    # B (the active session) is untouched -- the exact bug this closes.
    assert agent.session_id == "B"
    assert agent._history == ["b-msg"]


def test_reset_endpoint_with_no_body_resets_active_session_unchanged(isolated_cwd, monkeypatch):
    """Backward compatibility: every pre-existing client posts no body at
    all -- must behave exactly like the old always-reset-active endpoint."""
    agent = _StubAgent()
    client = _client(monkeypatch, agent)

    resp = client.post("/reset")

    assert resp.status_code == 200
    body = resp.json()
    assert body["archived_conversation_id"] == "B"
    assert agent.session_store.archived == ["B"]
    assert agent.session_id == body["session_id"]  # active pointer moved, as before


def test_reset_endpoint_with_empty_conversation_id_field_resets_active_session(
    isolated_cwd, monkeypatch
):
    agent = _StubAgent()
    client = _client(monkeypatch, agent)

    resp = client.post("/reset", json={"conversation_id": ""})

    assert resp.status_code == 200
    assert resp.json()["archived_conversation_id"] == "B"
