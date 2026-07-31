"""Faz 3 live-found fixes — regression guards for three real bugs surfaced
while A/B testing qwen3:8b (the first model to emit real tool calls, which
is what exposed them):

1. Non-streaming ainvoke() returns a dynamic interrupt as
   result["__interrupt__"] instead of raising GraphInterrupt on langgraph
   1.2.x — so /chat's `except GraphInterrupt` never fired and the whole
   confirmation payload was silently dropped (latent since Faz 4: only the
   streaming CLI/voice paths were ever live-verified).
2. The empty-response fallback scanned the ENTIRE message list, reaching
   back into replayed history and returning a PREVIOUS turn's answer
   verbatim — the "echo" failure seen live (D10/C8).
3. graph_stream_to_text only streamed the "agent" node; after Faz 2B the
   final answer of a tool turn comes from "compose", so an approved
   shell_run streamed back empty.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage


# ── 1. value-style interrupt surface in chat() ────────────────────────────────

@pytest.mark.asyncio
async def test_chat_raises_confirmation_from_value_style_interrupt(monkeypatch, jarvis_home):
    """A result carrying __interrupt__ must become ConfirmationRequired, not a
    dropped payload / empty answer."""
    from jarvis.agent import JarvisAgent, ConfirmationRequired

    agent = JarvisAgent.__new__(JarvisAgent)  # skip __init__; drive chat() directly

    interrupt_payload = {"tools": [{"name": "shell_run", "args": {"command": "dir"}, "id": "c1"}], "count": 1}

    class _Graph:
        async def ainvoke(self, state, config):
            return {"__interrupt__": [SimpleNamespace(value=interrupt_payload)], "messages": []}

    # Minimal collaborators chat() touches before the interrupt check.
    agent._graph = _Graph()
    agent._pending_confirmations = {}
    agent._last_turn_used_pro = False
    agent._turn = 0
    agent.session_id = "s1"
    agent._history = []
    agent.workspace = jarvis_home
    agent._env_static = ""   # _env_block is a property (live clock)
    agent.usage = SimpleNamespace()
    from jarvis.config import Settings
    agent.settings = Settings(_env_file=None)

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

    with pytest.raises(ConfirmationRequired) as ei:
        await agent.chat("shell aracıyla dir çalıştır", transport="api")
    assert ei.value.payload == interrupt_payload
    assert ei.value.conf_id in agent._pending_confirmations


# ── 2. empty-response fallback must not echo a previous turn ───────────────────

@pytest.mark.asyncio
async def test_empty_response_does_not_echo_history(monkeypatch, jarvis_home):
    from jarvis.agent import JarvisAgent

    agent = JarvisAgent.__new__(JarvisAgent)

    prior_answer = AIMessage(content="ÖNCEKİ TURN cevabı — sızmamalı")
    # initial_messages = system + prior history (1 msg) + this turn's human.
    # The graph returns only that same prefix + a blank AI (no new text).
    class _Graph:
        async def ainvoke(self, state, config):
            msgs = list(state["messages"]) + [AIMessage(content="")]
            return {"response": "", "messages": msgs}

    agent._graph = _Graph()
    agent._pending_confirmations = {}
    agent._last_turn_used_pro = False
    agent._turn = 1
    agent.session_id = "s1"
    agent._history = [prior_answer]
    agent.workspace = jarvis_home
    agent._env_static = ""   # _env_block is a property (live clock)
    agent.usage = SimpleNamespace()
    from jarvis.config import Settings
    agent.settings = Settings(_env_file=None)
    # current_model_label is a read-only property derived from _last_turn_*;
    # the values below make it resolve without a real trace.
    agent._last_turn_trace = None
    agent._last_turn_used_pro = False
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

    response, _ = await agent.chat("yeni soru", transport="api")
    assert "ÖNCEKİ TURN" not in response  # the echo bug
    assert response  # a non-empty controlled message instead


# ── 3. streaming includes the compose node ────────────────────────────────────

@pytest.mark.asyncio
async def test_stream_yields_compose_node_text():
    from jarvis.graph.streaming import graph_stream_to_text
    from langchain_core.messages import AIMessageChunk

    async def _astream(state, config, stream_mode):
        # tool-turn shape: agent emits only a tool call (no text), compose
        # produces the final answer
        yield AIMessageChunk(content="", tool_call_chunks=[
            {"name": "shell_run", "args": '{"command":"dir"}', "id": "c1", "index": 0},
        ]), {"langgraph_node": "agent", "langgraph_step": 1}
        yield AIMessageChunk(content="dir "), {"langgraph_node": "compose", "langgraph_step": 3}
        yield AIMessageChunk(content="çıktısı"), {"langgraph_node": "compose", "langgraph_step": 3}

    graph = SimpleNamespace(astream=_astream)
    out = "".join([t async for t in graph_stream_to_text(graph, {}, {})])
    assert out == "dir çıktısı"
