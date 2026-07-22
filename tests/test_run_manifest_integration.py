"""Agent Runtime rev.2, Faz 5 -- run_manifest.json actually gets written from
real JarvisAgent.chat()/chat_stream() turns, not just from write_run_manifest()
in isolation (tests/test_run_context.py's job).

Follows tests/test_interrupt_surface.py's established pattern for this
method: JarvisAgent.__new__() skips __init__ (constructing a real agent is
heavy), driven with a minimal set of collaborators standing in for exactly
what chat()/chat_stream() touch.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import AIMessage

from jarvis.agent import JarvisAgent
from jarvis.config import Settings


def _no_interrupt_graph() -> SimpleNamespace:
    """Faz 7.3: chat_stream()'s post-stream fallback
    (_pending_interrupt_payload) always calls self._graph.aget_state(),
    even on the plain-success path these tests exercise -- graph_stream_to_text
    itself is monkeypatched per-test, but _graph still needs this one real
    (mocked) method."""
    return SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(interrupts=())))


def _base_agent(jarvis_home, *, starting_turn: int = 0):
    """starting_turn is the value BEFORE chat()/chat_stream() run -- both
    increment self._turn near the top (agent.py's own `self._turn += 1`), so
    starting_turn=0 means this call becomes turn 1."""
    agent = JarvisAgent.__new__(JarvisAgent)
    agent._pending_confirmations = {}
    agent._last_turn_used_pro = False
    agent._turn = starting_turn
    agent.session_id = "s1"
    agent._history = []
    agent.workspace = jarvis_home
    agent._env_block = ""
    agent.usage = SimpleNamespace()
    agent.settings = Settings(_env_file=None)
    agent._last_turn_trace = None
    agent._active_model_id = None
    agent._effective_settings = agent.settings

    async def _noop_lock():
        return None
    agent._acquire_state_lock = _noop_lock
    agent._state_lock = SimpleNamespace(release=lambda: None)

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


def _manifest_path(jarvis_home, session_id="s1", turn=1):
    return jarvis_home / "data" / "runs" / f"{session_id}-t{turn}" / "run_manifest.json"


@pytest.mark.asyncio
async def test_chat_writes_a_run_manifest_on_success(jarvis_home):
    agent = _base_agent(jarvis_home)

    class _Graph:
        async def ainvoke(self, state, config):
            return {
                "response": "merhaba",
                "messages": list(state["messages"]) + [AIMessage(content="merhaba")],
                "execution_envelopes": [{"capability": "x", "status": "success"}],
            }
    agent._graph = _Graph()

    response, _ = await agent.chat("selam", transport="api")
    assert response == "merhaba"

    path = _manifest_path(jarvis_home)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "s1-t1"
    assert data["transport"] == "api"
    assert data["execution_envelopes"] == [{"capability": "x", "status": "success"}]
    assert "input_digest" in data and len(data["input_digest"]) == 64  # sha256 hex


@pytest.mark.asyncio
async def test_chat_manifest_omits_envelopes_key_gracefully_when_absent(jarvis_home):
    """mode="off" turns (the default) never populate execution_envelopes --
    the manifest write must not choke on a missing key."""
    agent = _base_agent(jarvis_home, starting_turn=1)

    class _Graph:
        async def ainvoke(self, state, config):
            return {"response": "ok", "messages": list(state["messages"]) + [AIMessage(content="ok")]}
    agent._graph = _Graph()

    await agent.chat("selam", transport="cli-text")

    data = json.loads(_manifest_path(jarvis_home, turn=2).read_text(encoding="utf-8"))
    assert data["execution_envelopes"] is None


@pytest.mark.asyncio
async def test_chat_stream_writes_a_run_manifest_on_success(jarvis_home, monkeypatch):
    agent = _base_agent(jarvis_home)
    agent._checkpointer = SimpleNamespace(get_tuple=lambda config: None)

    async def _fake_stream(graph, state, config):
        yield "merhaba"
    monkeypatch.setattr("jarvis.agent.graph_stream_to_text", _fake_stream)
    agent._graph = _no_interrupt_graph()

    chunks = [c async for c in agent.chat_stream("selam", transport="voice-cli")]
    assert "".join(chunks) == "merhaba"

    path = _manifest_path(jarvis_home)
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "s1-t1"
    assert data["transport"] == "voice-cli"
    assert data["execution_envelopes"] is None  # no checkpoint tuple in this fake


@pytest.mark.asyncio
async def test_chat_stream_manifest_survives_a_checkpointer_error(jarvis_home, monkeypatch):
    """get_tuple() raising must not prevent the manifest from being written
    (the checkpoint_tuple = None pre-init fix -- a real bug caught while
    wiring this in, not a hypothetical)."""
    agent = _base_agent(jarvis_home)

    def _boom(config):
        raise RuntimeError("checkpoint db locked")
    agent._checkpointer = SimpleNamespace(get_tuple=_boom)

    async def _fake_stream(graph, state, config):
        yield "ok"
    monkeypatch.setattr("jarvis.agent.graph_stream_to_text", _fake_stream)
    agent._graph = _no_interrupt_graph()

    chunks = [c async for c in agent.chat_stream("selam", transport="voice-cli")]
    assert "".join(chunks) == "ok"

    assert _manifest_path(jarvis_home).exists()
