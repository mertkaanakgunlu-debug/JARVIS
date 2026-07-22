"""Agent Runtime rev.2, Faz 5 follow-up -- real per-client conversation_id
support. Previously api.py's single shared JarvisAgent had exactly one
active session for every caller, and a server restart began fresh for every
client (see HANDOFF.md/MEMORY.md). This closes that gap:

  - SessionStore.ensure_session() (tested in test_session_store.py) lets a
    caller-chosen id become a REAL row instead of a phantom one invisible to
    list_sessions()/set_topic_hint().
  - JarvisAgent.switch_session()/_switch_session_locked() (tested here)
    register + resume via that new method -- following this suite's
    unbound-method precedent (see test_reset_lifecycle.py's docstring):
    constructing a real JarvisAgent is heavy, so the method runs against a
    minimal SimpleNamespace stand-in plus a REAL (tmp-file) SessionStore,
    since the store integration is exactly what's being verified.
  - jarvis.api's ChatRequest/ChatResponse conversation_id field (tested here
    via the TestClient+StubAgent pattern test_api_upload.py established).

chat()/chat_stream() themselves are NOT exercised end-to-end here --
constructing a real JarvisAgent to drive a real turn is this suite's
established heavy path (LLM providers, memory, the compiled graph). Their
conversation_id handling is a two-line addition inside the SAME locked
section chat()'s own docstring already documents as atomic for
_history/_turn/session_id; verified by reading (the switch happens inside
the existing `try:` under `_state_lock`, never as a separate pre-call
step), not by a dedicated concurrency harness -- matching how BUG-8's
original whole-turn lock was itself verified live rather than left as a
perpetual automated stress test.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage
from starlette.testclient import TestClient

import jarvis.api as api
from jarvis.agent import JarvisAgent
from jarvis.session_store import SessionStore


# ── JarvisAgent.switch_session() / _switch_session_locked() ─────────────────

def _fake_agent(tmp_path) -> SimpleNamespace:
    agent = SimpleNamespace(
        _state_lock=threading.Lock(),
        session_id="s0",
        _history=[],
        _turn=0,
        session_store=SessionStore(tmp_path / "sessions.db"),
    )
    # switch_session() calls self._switch_session_locked(...) -- SimpleNamespace
    # has no bound methods of its own, so wire it the same way
    # test_reset_lifecycle.py wires _reset_state_sync onto its own fake.
    agent._switch_session_locked = lambda sid: JarvisAgent._switch_session_locked(agent, sid)
    return agent


def test_switch_session_to_a_brand_new_id_registers_a_real_row(tmp_path):
    """The core gap this closes: previously switch_session() adopted ANY id
    with no existence check, leaving it invisible to list_sessions()."""
    agent = _fake_agent(tmp_path)

    n = JarvisAgent.switch_session(agent, "client-minted-uuid")

    assert n == 0
    assert agent.session_id == "client-minted-uuid"
    assert agent._history == []
    assert agent._turn == 0
    assert agent.session_store.session_exists("client-minted-uuid")
    assert any(
        s["id"] == "client-minted-uuid" for s in agent.session_store.list_sessions(50)
    )


def test_switch_session_to_an_existing_id_resumes_history_and_turn(tmp_path):
    """Regression: a real past session must still resume exactly as before
    -- BUG-11's turn-counter resume in particular."""
    agent = _fake_agent(tmp_path)
    prior = agent.session_store.new_session()
    msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
    agent.session_store.save_turn(prior, msgs, turn_idx=3)

    n = JarvisAgent.switch_session(agent, prior)

    assert n == 2
    assert agent.session_id == prior
    assert agent._turn == 3
    assert [m.content for m in agent._history] == ["hi", "hello"]


def test_switch_session_releases_the_lock_afterward(tmp_path):
    agent = _fake_agent(tmp_path)
    JarvisAgent.switch_session(agent, "some-id")
    assert agent._state_lock.acquire(blocking=False)
    agent._state_lock.release()


def test_switch_session_locked_does_not_reacquire_the_lock(tmp_path):
    """_switch_session_locked() assumes the caller already holds
    _state_lock (chat()/chat_stream()'s use case) -- it must not try to
    acquire it again itself, which would deadlock (threading.Lock is not
    reentrant)."""
    agent = _fake_agent(tmp_path)
    with agent._state_lock:
        n = JarvisAgent._switch_session_locked(agent, "mid-turn-switch")
    assert n == 0
    assert agent.session_id == "mid-turn-switch"
    assert agent.session_store.session_exists("mid-turn-switch")


# ── jarvis.api: conversation_id wiring ───────────────────────────────────────

class _StubAgent:
    """Just enough of JarvisAgent's surface for /chat, /chat/stream, /reset.
    Mimics the real side effect being tested: chat()/chat_stream() adopt
    conversation_id onto self.session_id before the endpoint reads it back."""

    def __init__(self):
        self.session_id = "initial-session"
        self.received_kwargs: dict = {}

    async def chat(self, message, **kwargs):
        self.received_kwargs = kwargs
        if kwargs.get("conversation_id"):
            self.session_id = kwargs["conversation_id"]
        return f"echo:{message}", "stub-model"

    async def chat_stream(self, message, **kwargs):
        self.received_kwargs = kwargs
        if kwargs.get("conversation_id"):
            self.session_id = kwargs["conversation_id"]
        yield "ok"

    async def reset_async(self):
        self.session_id = "fresh-after-reset"


def _client(monkeypatch, agent) -> TestClient:
    # Same precedent as test_api_upload.py's _client(): monkeypatch the
    # module globals directly instead of init_agent(), so no real JarvisAgent
    # is constructed and no ASGI lifespan (which would start a real
    # monitor/voice stack) ever runs. _settings=None disables auth.
    monkeypatch.setattr(api, "_agent", agent)
    monkeypatch.setattr(api, "_settings", None)
    return TestClient(api.app)


def test_chat_forwards_conversation_id_and_echoes_active_session(monkeypatch):
    agent = _StubAgent()
    client = _client(monkeypatch, agent)

    resp = client.post("/chat", json={"message": "hi", "conversation_id": "conv-A"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] == "conv-A"
    assert agent.received_kwargs["conversation_id"] == "conv-A"


def test_chat_with_no_conversation_id_is_a_complete_noop(monkeypatch):
    """Zero behavior change for every pre-existing client (Electron HUD,
    mobile app) that doesn't send this field yet."""
    agent = _StubAgent()
    client = _client(monkeypatch, agent)

    resp = client.post("/chat", json={"message": "hi"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["conversation_id"] == "initial-session"
    assert agent.received_kwargs["conversation_id"] == ""


def test_chat_stream_forwards_conversation_id(monkeypatch):
    agent = _StubAgent()
    client = _client(monkeypatch, agent)

    resp = client.post("/chat/stream", json={"message": "hi", "conversation_id": "conv-B"})

    assert resp.status_code == 200
    assert agent.received_kwargs["conversation_id"] == "conv-B"


def test_reset_response_echoes_the_new_session_id(monkeypatch):
    agent = _StubAgent()
    client = _client(monkeypatch, agent)

    resp = client.post("/reset")

    assert resp.status_code == 200
    assert resp.json()["session_id"] == "fresh-after-reset"
