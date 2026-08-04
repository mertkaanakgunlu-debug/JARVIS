"""Post-MVP Faz 2.75, Paket B — a turn belongs to a conversation.

One JarvisAgent serves every API client, and `self.session_id` is whatever the
most recent request switched it to. Three places read that global instead of
the conversation the work actually belonged to:

    A asks for something risky   -> parked on a confirmation
    B sends an ordinary message  -> the agent's active session is now B
    A approves                   -> A's answer was appended to B's history

and the same shape for a background task, which reads the active session when a
worker picks it up rather than when the request submitted it. The confirm
endpoint took no conversation at all, so "approve whatever is pending" was a
sentence any client could say about someone else's L3 external write.
"""
from __future__ import annotations

import asyncio
import json
import threading
import types
from types import SimpleNamespace

import pytest

import jarvis.agent as agent_mod
from jarvis.agent import JarvisAgent


# ── the pending record names its conversation ─────────────────────────────────

class _Registrar:
    """Only what _register_pending_confirmation touches."""

    def __init__(self, session_id: str):
        from jarvis.config import Settings

        self.session_id = session_id
        self.settings = Settings(_env_file=None)
        self._pending_confirmations: dict = {}


def test_a_pending_confirmation_records_whose_turn_it_is():
    agent = _Registrar("conv-A")
    JarvisAgent._register_pending_confirmation(agent, "c1", {"cfg": 1}, None)
    assert agent._pending_confirmations["c1"]["conversation_id"] == "conv-A"


def test_the_record_still_carries_what_it_always_did():
    agent = _Registrar("conv-A")
    JarvisAgent._register_pending_confirmation(agent, "c1", {"cfg": 1}, "rec")
    entry = agent._pending_confirmations["c1"]
    assert entry["config"] == {"cfg": 1}
    assert entry["recorder"] == "rec"
    assert "created_at" in entry


# ── resume writes into the pinned conversation, not the active one ────────────

async def _fake_stream(graph, command, config):
    yield "onaylanan cevap"


class _FakeResumeAgent:
    """Exactly the attributes resume_and_stream() reads/writes, with a session
    store that remembers which conversation each write went to."""

    def __init__(self, pending: dict, active: str):
        from jarvis.config import Settings

        self._state_lock = threading.Lock()
        self._pending_confirmations = dict(pending)
        self.session_id = active
        self._history: list = []
        self._turn = 3
        self._graph = SimpleNamespace(
            aget_state=lambda cfg: _empty_snapshot(),
        )
        self._last_turn_trace = None
        self._record_turn_trace = lambda trace: None
        self._checkpointer = SimpleNamespace(get_tuple=lambda cfg: None)
        # Post-MVP Faz 6: the real method, not a stub -- see its docstring for
        # why the buffering decision has to be read from the checkpoint here.
        self._contract_buffered = types.MethodType(JarvisAgent._contract_buffered, self)
        self.settings = Settings(_env_file=None)
        self.saved: list[tuple] = []
        self.stored: list[tuple] = []
        self.switched_to: list[str] = []
        self.session_store = SimpleNamespace(
            save_turn=lambda sid, hist, turn: self.saved.append((sid, list(hist), turn)),
            ensure_session=lambda sid: None,
            load_history=lambda sid, limit=20: [],
            last_turn_idx=lambda sid: 7,
        )
        self.memory = SimpleNamespace(
            store=lambda role, text, sid: self.stored.append((role, text, sid)),
            log_turn=lambda *a, **k: None,
        )

    async def _acquire_state_lock(self):
        await asyncio.get_running_loop().run_in_executor(None, self._state_lock.acquire)

    def _switch_session_locked(self, session_id: str) -> int:
        self.switched_to.append(session_id)
        return JarvisAgent._switch_session_locked(self, session_id)

    def _schedule_memory_extraction(self, user_text, response):
        pass

    async def _pending_interrupt_payload(self, config):
        return None


async def _empty_snapshot():
    return SimpleNamespace(interrupts=())


def _pending(conversation_id: str) -> dict:
    return {"c1": {
        "config": {"configurable": {"thread_id": "t1"}},
        "recorder": None, "created_at": 0.0,
        "conversation_id": conversation_id,
    }}


async def test_resume_writes_into_the_conversation_the_turn_belongs_to(monkeypatch):
    """The acceptance scenario, minus the model.

    A's confirmation was raised while A was active; B has spoken since, so the
    agent's active session is B. The approved answer must still land in A.
    """
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent(_pending("conv-A"), active="conv-B")

    [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert agent.switched_to == ["conv-A"], "the turn must be re-pinned before writing"
    assert agent.saved, "the completed turn must still be persisted"
    assert {sid for sid, _h, _t in agent.saved} == {"conv-A"}
    assert {sid for _r, _t, sid in agent.stored} == {"conv-A"}


async def test_no_switch_when_the_conversation_is_already_active(monkeypatch):
    """The single-client case -- the overwhelmingly common one -- must not pay
    a history reload on every approval."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent(_pending("conv-A"), active="conv-A")

    [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert agent.switched_to == []
    assert {sid for sid, _h, _t in agent.saved} == {"conv-A"}


async def test_an_old_pending_entry_without_a_conversation_still_resumes(monkeypatch):
    """Entries registered before this field existed have no conversation_id.
    They must resume into the active session rather than crash or refuse --
    the alternative would strand a real approval on an upgrade."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    legacy = {"c1": {"config": {"configurable": {"thread_id": "t1"}},
                     "recorder": None, "created_at": 0.0}}
    agent = _FakeResumeAgent(legacy, active="conv-B")

    out = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert "".join(out) == "onaylanan cevap"
    assert agent.switched_to == []


# ── a client cannot approve someone else's confirmation ───────────────────────

async def test_a_mismatched_conversation_id_is_refused(monkeypatch):
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent(_pending("conv-A"), active="conv-A")

    out = "".join([
        c async for c in
        JarvisAgent.resume_and_stream(agent, "c1", "approve", conversation_id="conv-B")
    ])

    assert "different conversation" in out
    assert agent.saved == [], "a refused resume must not run the graph or persist"


async def test_a_matching_conversation_id_is_accepted(monkeypatch):
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent(_pending("conv-A"), active="conv-A")

    out = "".join([
        c async for c in
        JarvisAgent.resume_and_stream(agent, "c1", "approve", conversation_id="conv-A")
    ])

    assert out == "onaylanan cevap"


async def test_omitting_the_conversation_id_still_works(monkeypatch):
    """Optional on purpose: the CLI and the voice loops have one conversation
    and never send it. Requiring it would break every existing caller."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent(_pending("conv-A"), active="conv-A")

    out = "".join([c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")])
    assert out == "onaylanan cevap"


# ── a background task remembers who asked ─────────────────────────────────────

def test_submit_captures_the_conversation(isolated_cwd):
    from jarvis.task_executor import TaskExecutor

    executor = TaskExecutor(agent=None)
    task = executor.submit("uzun bir iş", "conv-A")
    assert task.conversation_id == "conv-A"
    assert task.to_dict()["conversation_id"] == "conv-A"


def test_submit_without_a_conversation_is_still_allowed(isolated_cwd):
    from jarvis.task_executor import TaskExecutor

    executor = TaskExecutor(agent=None)
    assert executor.submit("uzun bir iş").conversation_id == ""


def test_the_worker_passes_the_captured_conversation_through():
    """Source-level: driving _run() needs a real agent and a thread pool, and a
    fake big enough to run it would re-implement the thing under test."""
    import inspect

    from jarvis.task_executor import TaskExecutor

    src = inspect.getsource(TaskExecutor._run)
    assert "conversation_id=task.conversation_id" in src


def test_background_turn_prefers_the_given_conversation():
    import inspect

    src = inspect.getsource(JarvisAgent.background_turn)
    assert "origin_session_id = conversation_id or self.session_id" in src


# ── the API carries it ────────────────────────────────────────────────────────

def test_confirm_request_accepts_a_conversation_id():
    from jarvis.api import ConfirmRequest

    assert ConfirmRequest(decision="approve").conversation_id == ""
    assert ConfirmRequest(decision="approve", conversation_id="c").conversation_id == "c"


def test_the_confirm_endpoint_forwards_it():
    import inspect

    import jarvis.api as api

    src = inspect.getsource(api.chat_confirm)
    assert "conversation_id=body.conversation_id" in src


@pytest.mark.parametrize("marker", ["conversation_id"])
def test_the_chat_endpoints_forward_it_to_the_executor(marker):
    import inspect

    import jarvis.api as api

    src = inspect.getsource(api)
    assert src.count('executor.submit(body.message, getattr(body, "conversation_id", "") or "")') == 2, (
        "both the /chat and /chat/stream offload paths must capture it"
    )


def test_json_shape_of_a_task_is_backwards_compatible():
    """The mobile client reads this dict; adding a key is safe, renaming one is
    not."""
    from jarvis.task_executor import AsyncTask

    d = AsyncTask(task_id="t", user_query="q").to_dict()
    for key in ("task_id", "user_query", "status", "created_at"):
        assert key in d
    assert json.dumps(d)
