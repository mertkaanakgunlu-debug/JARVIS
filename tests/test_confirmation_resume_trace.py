"""resume_and_stream() — the confirmation round-trip's trace rollup (patch 1.1).

chat()/chat_stream() register an LlmTraceRecorder in the graph config's
callbacks before invoking; when the graph interrupts for confirmation, they
now park {"config", "recorder"} (not just the bare config) in
_pending_confirmations. resume_and_stream() continues the SAME turn on the
same config -- so the recorder keeps accumulating the post-approval LLM calls
-- and must roll it up into _last_turn_trace when the turn finally completes.
Before this fix the rollup never happened: /status kept displaying the
PREVIOUS turn's provider/model for any turn that went through a confirmation.

Follows this suite's unbound-method precedent (test_background_turn.py,
test_reset_lifecycle.py): a real JarvisAgent is heavy, so the method runs
against a minimal stand-in exposing exactly what it touches;
graph_stream_to_text is monkeypatched at the jarvis.agent import site so no
compiled graph/checkpointer machinery is needed.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

import jarvis.agent as agent_mod
from jarvis.agent import JarvisAgent
from jarvis.llm_trace import LlmTraceRecorder


def _llm_result(tokens_in=10, tokens_out=5):
    msg = AIMessage(
        content="hi",
        usage_metadata={"input_tokens": tokens_in, "output_tokens": tokens_out,
                        "total_tokens": tokens_in + tokens_out},
    )
    return SimpleNamespace(generations=[[SimpleNamespace(message=msg)]], llm_output={})


def _recorder_with_one_ollama_success() -> LlmTraceRecorder:
    """Simulates the callbacks the resumed graph run fires into the recorder
    that chat_stream() registered before the interrupt."""
    rec = LlmTraceRecorder(requested_role="fast")
    rid = uuid4()
    rec.on_chat_model_start({}, None, run_id=rid, metadata={
        "jarvis_provider": "ollama", "jarvis_model": "qwen2.5:7b-instruct",
        "jarvis_billing": "free", "jarvis_tier_index": 0, "langgraph_node": "agent",
    })
    rec.on_llm_end(_llm_result(120, 40), run_id=rid)
    return rec


_STALE_TRACE = {"provider": "vertex", "model": "STALE-FROM-PREVIOUS-TURN"}


class _FakeResumeAgent:
    """Exactly the attributes resume_and_stream() reads/writes."""

    def __init__(self, pending: dict):
        self._state_lock = threading.Lock()
        self._pending_confirmations = dict(pending)
        self._history = []
        self._turn = 1
        self.session_id = "s1"
        self._graph = None  # graph_stream_to_text is monkeypatched, never touches it
        self._last_turn_trace = dict(_STALE_TRACE)
        # get_tuple -> None makes the method take its documented fallback
        # path (rebuild history manually) -- no checkpointer machinery needed.
        self._checkpointer = SimpleNamespace(get_tuple=lambda cfg: None)
        self.saved_turns: list[tuple] = []
        self.session_store = SimpleNamespace(
            save_turn=lambda sid, hist, turn: self.saved_turns.append((sid, list(hist), turn)),
        )
        self.memory = SimpleNamespace(store=lambda *a, **k: None, log_turn=lambda *a, **k: None)

    async def _acquire_state_lock(self):
        await asyncio.get_running_loop().run_in_executor(None, self._state_lock.acquire)

    def _schedule_memory_extraction(self, user_text, response):
        pass


async def _fake_stream(graph, command, config):
    yield "resumed "
    yield "answer"


@pytest.mark.asyncio
async def test_resume_rolls_recorder_up_into_last_turn_trace(monkeypatch):
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    recorder = _recorder_with_one_ollama_success()
    agent = _FakeResumeAgent({
        "c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": recorder},
    })

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert "".join(chunks) == "resumed answer"
    assert agent._last_turn_trace["provider"] == "ollama"
    assert agent._last_turn_trace["model"] == "qwen2.5:7b-instruct"
    assert agent._pending_confirmations == {}, "the pending entry must be consumed"
    assert agent.saved_turns, "the completed turn must still be persisted"
    assert agent._state_lock.acquire(blocking=False), "lock must be released"
    agent._state_lock.release()


@pytest.mark.asyncio
async def test_resume_without_recorder_keeps_previous_trace(monkeypatch):
    """A pending entry carrying no recorder (defensive: e.g. state built by
    older code) must resume fine and simply leave the label untouched --
    'keep the old label rather than lie', same rule as turn_summary()=None."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent({
        "c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None},
    })

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert "".join(chunks) == "resumed answer"
    assert agent._last_turn_trace == _STALE_TRACE


@pytest.mark.asyncio
async def test_resume_with_unknown_or_reset_cleared_id_yields_error():
    """After patch 1.1's reset fix, _reset_state_sync() clears
    _pending_confirmations -- a pre-reset conf_id must land here (expired),
    never resume a graph into the new session."""
    agent = _FakeResumeAgent({})

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "gone", "approve")]

    assert any("expired" in c for c in chunks)
    assert agent.saved_turns == []
