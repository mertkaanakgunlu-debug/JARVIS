"""Faz 7.3 (P1) -- chat_stream()/resume_and_stream() must detect a
confirmation interrupt even when the underlying LangGraph run does NOT
raise GraphInterrupt.

Found live, not hypothesized: an E2E pass against a real `python -m jarvis
--api --profile test` server, real local Ollama model, showed /chat
correctly returning {"confirmation_required": true, ...} for a
shell_run-triggering prompt while /chat/stream returned ONLY "data:
[DONE]" -- no marker, no error -- for the byte-identical prompt, 5/5
reproductions. Root-caused with a minimal LangGraph repro: on the
installed langgraph version, `astream(stream_mode="messages")` does NOT
raise GraphInterrupt when a node calls interrupt() -- the generator just
ends normally, with the pending interrupt sitting in
`(await graph.aget_state(config)).interrupts`. This is the exact same
shape as the ALREADY-FIXED chat()/ainvoke() gap documented in agent.py's
own "Faz 3 live A/B finding" comment -- ainvoke() doesn't raise either,
and returns `result["__interrupt__"]` instead. chat_stream() was
apparently never live-verified against a REAL interrupt over streaming
(the comment's own claim that "the streaming CLI/voice paths raise" does
not hold on this langgraph version) -- meaning a confirmation-required
action (email send, calendar delete, shell exec, ...) asked for via
voice, the Electron HUD, or any /chat/stream client could vanish
completely: no prompt, no error, nothing, and the action silently never
runs (fails closed, not open -- but silently, which is its own bug).

Both entry points share one fix: _pending_interrupt_payload() reads
graph.aget_state(config).interrupts directly once the stream ends without
raising. These tests exercise it directly against real JarvisAgent
methods (unbound-method precedent, same as test_confirmation_resume_trace.py
/ test_run_manifest_integration.py) with a controllable fake graph.
"""
from __future__ import annotations

import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.errors import GraphInterrupt

from jarvis.agent import JarvisAgent
from jarvis.config import Settings


def _interrupt_snapshot(payload: dict) -> SimpleNamespace:
    interrupt = SimpleNamespace(value=payload)
    return SimpleNamespace(interrupts=(interrupt,))


def _empty_snapshot() -> SimpleNamespace:
    return SimpleNamespace(interrupts=())


def _base_chat_stream_agent(*, aget_state_return, workspace):
    agent = JarvisAgent.__new__(JarvisAgent)
    agent._pending_confirmations = {}
    agent._last_turn_used_pro = False
    agent._turn = 0
    agent.session_id = "s1"
    agent._history = []
    agent.workspace = workspace  # write_run_manifest() needs a real path on the no-interrupt path
    agent._env_static = ""   # _env_block is a property (live clock)
    agent.usage = SimpleNamespace()
    agent.settings = Settings(_env_file=None)
    agent._last_turn_trace = None
    agent._active_model_id = None
    agent._effective_settings = agent.settings
    agent._state_lock = threading.Lock()
    agent._checkpointer = SimpleNamespace(get_tuple=lambda config: None)
    agent._graph = SimpleNamespace(aget_state=AsyncMock(return_value=aget_state_return))

    async def _noop_lock():
        await asyncio.get_running_loop().run_in_executor(None, agent._state_lock.acquire)
    agent._acquire_state_lock = _noop_lock

    async def _connect():
        return None
    agent.connect_mcp_tools = _connect
    agent._context_builder = SimpleNamespace(
        build=lambda *a, **k: SimpleNamespace(
            memory_ctx="", entities_block="", past_sessions_block="",
            open_todos_block="", facts_block="", procedure_block="",
        )
    )
    agent.session_store = SimpleNamespace(
        save_turn=lambda *a, **k: None, set_topic_hint=lambda *a, **k: None,
    )
    agent.memory = SimpleNamespace(store=lambda *a, **k: None, log_turn=lambda *a, **k: None)
    agent._schedule_memory_extraction = lambda *a, **k: None
    return agent


async def _fake_empty_stream(graph, state, config):
    return
    yield  # pragma: no cover -- makes this an async generator that yields nothing


async def _fake_text_stream(graph, state, config):
    yield "just talking, "
    yield "no tool call"


# ── chat_stream(): the core fix ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_stream_detects_an_interrupt_the_stream_never_raised(monkeypatch, tmp_path):
    """The exact live-observed bug: graph_stream_to_text yields NOTHING (no
    GraphInterrupt raised), but a real interrupt IS sitting in graph state."""
    payload = {"tools": [{"name": "shell_run", "args": {"command": "echo hi"}}]}
    agent = _base_chat_stream_agent(aget_state_return=_interrupt_snapshot(payload), workspace=tmp_path)
    monkeypatch.setattr("jarvis.agent.graph_stream_to_text", _fake_empty_stream)

    chunks = [c async for c in agent.chat_stream("run echo hi", transport="api-stream")]

    assert len(chunks) == 1
    marker = json.loads(chunks[0])
    assert marker["__jarvis_confirm__"] is True
    assert marker["payload"] == payload
    assert marker["id"] in agent._pending_confirmations
    assert agent._pending_confirmations[marker["id"]]["config"] is not None


@pytest.mark.asyncio
async def test_chat_stream_normal_completion_is_unaffected(monkeypatch, tmp_path):
    """No interrupt anywhere -- must stream real text and never touch the
    confirmation machinery (the aget_state() call itself still happens --
    it's unconditional -- but must be a harmless no-op)."""
    agent = _base_chat_stream_agent(aget_state_return=_empty_snapshot(), workspace=tmp_path)
    monkeypatch.setattr("jarvis.agent.graph_stream_to_text", _fake_text_stream)

    chunks = [c async for c in agent.chat_stream("hello", transport="api-stream")]

    assert "".join(chunks) == "just talking, no tool call"
    assert agent._pending_confirmations == {}
    agent._graph.aget_state.assert_awaited()


@pytest.mark.asyncio
async def test_chat_stream_lock_is_released_after_interrupt_fallback(monkeypatch, tmp_path):
    payload = {"tools": []}
    agent = _base_chat_stream_agent(aget_state_return=_interrupt_snapshot(payload), workspace=tmp_path)
    monkeypatch.setattr("jarvis.agent.graph_stream_to_text", _fake_empty_stream)

    [c async for c in agent.chat_stream("run echo hi", transport="api-stream")]

    assert agent._state_lock.acquire(blocking=False), "lock must be released, not held forever"
    agent._state_lock.release()


# ── resume_and_stream(): the same fallback, for a SECOND interrupt mid-resume ──

def _resume_agent(pending: dict, *, aget_state_return):
    agent = SimpleNamespace(
        _state_lock=threading.Lock(),
        _pending_confirmations=dict(pending),
        _history=[],
        _turn=1,
        session_id="s1",
        _graph=SimpleNamespace(aget_state=AsyncMock(return_value=aget_state_return)),
        _last_turn_trace=None,
        _checkpointer=SimpleNamespace(get_tuple=lambda cfg: None),
        settings=Settings(_env_file=None),
        session_store=SimpleNamespace(save_turn=lambda *a, **k: None),
        memory=SimpleNamespace(store=lambda *a, **k: None, log_turn=lambda *a, **k: None),
    )

    async def _acquire():
        await asyncio.get_running_loop().run_in_executor(None, agent._state_lock.acquire)
    agent._acquire_state_lock = _acquire

    def _noop(*a, **k):
        pass
    agent._schedule_memory_extraction = _noop
    # SimpleNamespace isn't a JarvisAgent -- wire the real unbound method the
    # same way every other agent.py collaborator is bound onto this fake.
    agent._pending_interrupt_payload = (
        lambda cfg: JarvisAgent._pending_interrupt_payload(agent, cfg)
    )
    agent._register_pending_confirmation = (
        lambda conf_id, config, recorder: JarvisAgent._register_pending_confirmation(
            agent, conf_id, config, recorder
        )
    )
    # Post-MVP Faz 6: resume_and_stream asks whether this turn's answer is
    # buffered until the graph finishes. Real method, same reason as the two
    # above -- a local `lambda cfg: False` would keep these tests green while
    # the streaming decision drifted.
    agent._contract_buffered = lambda cfg: JarvisAgent._contract_buffered(agent, cfg)
    return agent


@pytest.mark.asyncio
async def test_resume_and_stream_detects_a_second_interrupt(monkeypatch):
    """Approving one L3 action, then the agent wants ANOTHER confirmable
    action in the SAME (resumed) turn -- must surface a fresh confirmation,
    not silently vanish."""
    payload = {"tools": [{"name": "google_calendar", "args": {"action": "delete"}}]}
    agent = _resume_agent(
        {"c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None}},
        aget_state_return=_interrupt_snapshot(payload),
    )
    monkeypatch.setattr("jarvis.agent.graph_stream_to_text", _fake_empty_stream)

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert len(chunks) == 1
    marker = json.loads(chunks[0])
    assert marker["__jarvis_confirm__"] is True
    assert marker["payload"] == payload
    # A NEW conf_id -- distinct from the one just consumed.
    assert marker["id"] != "c1"
    assert marker["id"] in agent._pending_confirmations
    assert "c1" not in agent._pending_confirmations  # the original was already popped


@pytest.mark.asyncio
async def test_resume_and_stream_without_a_second_interrupt_completes_normally(monkeypatch):
    agent = _resume_agent(
        {"c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None}},
        aget_state_return=_empty_snapshot(),
    )
    monkeypatch.setattr("jarvis.agent.graph_stream_to_text", _fake_text_stream)

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert "".join(chunks) == "just talking, no tool call"
    assert agent._pending_confirmations == {}


@pytest.mark.asyncio
async def test_resume_and_stream_uses_pre_claimed_without_touching_the_dict(monkeypatch):
    """Review remediation (cross-transport claim race): a caller that
    already atomically claimed the entry via claim_pending_confirmation()
    (popping it out of _pending_confirmations itself) passes it through
    via pre_claimed -- resume_and_stream() must use THAT dict directly,
    never look conf_id up in _pending_confirmations again (which would
    either find nothing, since the caller already popped it, or -- if
    something else had re-inserted the same id in the meantime -- resume
    against the WRONG entry)."""
    agent = _resume_agent({}, aget_state_return=_empty_snapshot())  # conf_id NOT in the dict at all
    monkeypatch.setattr("jarvis.agent.graph_stream_to_text", _fake_text_stream)
    pre_claimed = {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None}

    chunks = [
        c async for c in
        JarvisAgent.resume_and_stream(agent, "c1", "approve", pre_claimed=pre_claimed)
    ]

    assert "".join(chunks) == "just talking, no tool call"
    assert agent._pending_confirmations == {}  # never touched -- nothing to pop


@pytest.mark.asyncio
async def test_resume_and_stream_handles_graph_interrupt_raised_directly(monkeypatch):
    """Review remediation: resume_and_stream() previously had no
    `except GraphInterrupt` handler at all -- unlike chat_stream(), which
    keeps one as defense-in-depth even though it's dead on this LangGraph
    version. If GraphInterrupt were ever raised here (e.g. a LangGraph
    version change), it used to fall into the generic `except Exception`
    and silently become an opaque "[ERROR: ...]" string instead of a
    confirmation prompt for the second interrupt."""
    payload = {"tools": [{"name": "shell_run", "args": {"command": "echo hi"}}]}

    async def _raises_graph_interrupt(graph, command, config):
        raise GraphInterrupt((SimpleNamespace(value=payload),))
        yield  # pragma: no cover -- unreachable, keeps this an async generator

    agent = _resume_agent(
        {"c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None}},
        aget_state_return=_empty_snapshot(),
    )
    monkeypatch.setattr("jarvis.agent.graph_stream_to_text", _raises_graph_interrupt)

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert len(chunks) == 1
    marker = json.loads(chunks[0])
    assert marker["__jarvis_confirm__"] is True
    assert marker["payload"] == payload
    assert marker["id"] in agent._pending_confirmations
    assert "c1" not in agent._pending_confirmations
    assert agent._state_lock.acquire(blocking=False), "lock must be released, not held forever"
    agent._state_lock.release()
