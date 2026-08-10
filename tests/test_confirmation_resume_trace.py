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
import json
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock
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
        # graph_stream_to_text is monkeypatched, so _graph itself is never
        # touched there -- but Faz 7.3's post-stream interrupt fallback
        # (_pending_interrupt_payload) DOES call self._graph.aget_state()
        # directly, every time, so it needs a real (mocked) return value:
        # an empty snapshot.interrupts, i.e. "no second interrupt happened".
        self._graph = SimpleNamespace(aget_state=AsyncMock(return_value=SimpleNamespace(interrupts=())))
        self._last_turn_trace = dict(_STALE_TRACE)
        # The REAL setter, not a stub: resume_and_stream stores the rolled-up
        # trace through it (it also broadcasts model_status to the HUD), and
        # this test exists to assert exactly that storage happens. A local
        # reimplementation could drift from production and still pass.
        self._record_turn_trace = types.MethodType(
            JarvisAgent._record_turn_trace, self
        )
        # Post-MVP Faz 6: resume_and_stream now asks whether this turn's
        # answer should be buffered until the graph finishes. Borrowed from
        # the real class for the same reason as the setter above -- a local
        # stub would let the streaming decision drift from production while
        # every test here still passed.
        self._contract_buffered = types.MethodType(
            JarvisAgent._contract_buffered, self
        )
        # get_tuple -> None makes the method take its documented fallback
        # path (rebuild history manually) -- no checkpointer machinery needed.
        self._checkpointer = SimpleNamespace(get_tuple=lambda cfg: None)
        # Patch 1.2 (Faz 1D): resume_and_stream now reads
        # settings.max_conversation_turns for the turn-based history window.
        from jarvis.config import Settings
        self.settings = Settings(_env_file=None)
        self.saved_turns: list[tuple] = []
        self.session_store = SimpleNamespace(
            save_turn=lambda sid, hist, turn: self.saved_turns.append((sid, list(hist), turn)),
        )
        self.memory = SimpleNamespace(store=lambda *a, **k: None, log_turn=lambda *a, **k: None)

    async def _acquire_state_lock(self):
        await asyncio.get_running_loop().run_in_executor(None, self._state_lock.acquire)

    def _schedule_memory_extraction(self, user_text, response):
        pass

    async def _pending_interrupt_payload(self, config):
        return await JarvisAgent._pending_interrupt_payload(self, config)


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


# ── the user's own words survive a confirmed turn (external review, 2026-08-01) ──

@pytest.mark.asyncio
async def test_resume_stores_the_user_query_not_just_the_answer(monkeypatch):
    """A confirmed turn used to persist only JARVIS's reply to memory.

    resume_and_stream reads user_query out of the checkpoint (it needs it to
    build the history exchange) and then passed "" on to memory.store and
    _schedule_memory_extraction. So on exactly the turns that ran a risky,
    confirmed action, episodic memory kept "I sent it" without "send Baran the
    report", and fact/entity extraction saw only the assistant side.
    chat(), chat_stream() and background_turn() all store both.
    """
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent({
        "c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None},
    })
    # A checkpoint that HAS the original request -- the fallback get_tuple=None
    # in _FakeResumeAgent would hide the whole point of this test.
    agent._checkpointer = SimpleNamespace(get_tuple=lambda cfg: SimpleNamespace(
        checkpoint={"channel_values": {
            "user_query": "Baran'a raporu gönder", "tool_execution_ledger": [],
        }},
    ))
    stored: list[tuple] = []
    extracted: list[tuple] = []
    agent.memory = SimpleNamespace(
        store=lambda role, text, sid: stored.append((role, text)),
        log_turn=lambda *a, **k: None,
    )
    agent._schedule_memory_extraction = lambda user_text, response: extracted.append(
        (user_text, response)
    )

    [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert ("user", "Baran'a raporu gönder") in stored
    assert ("assistant", "resumed answer") in stored
    assert extracted == [("Baran'a raporu gönder", "resumed answer")]


# ── the graph's terminal answer, not the discarded draft (Faz 6 önkoşulu) ──

def _checkpoint(**channel_values):
    return SimpleNamespace(checkpoint={"channel_values": {
        "tool_execution_ledger": [], **channel_values,
    }})


@pytest.mark.asyncio
async def test_resume_persists_the_terminal_response_not_the_stream(monkeypatch):
    """chat_stream() switched to the checkpoint's terminal `response` in Faz
    2.75 (Paket A); this path kept rebuilding the answer from its own chunks.

    Everything that REPLACES an answer after it was produced -- the critic's
    revision, `verify`'s unbacked-claim repair, its honest-failure block --
    writes state["response"] and keeps its tokens out of the stream. So on a
    confirmed turn, history/memory got the draft that was thrown away.
    """
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent({
        "c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None},
    })
    agent._checkpointer = SimpleNamespace(get_tuple=lambda cfg: _checkpoint(
        user_query="Baran'a raporu gönder", response="corrected answer",
    ))
    stored: list[tuple] = []
    agent.memory = SimpleNamespace(
        store=lambda role, text, sid: stored.append((role, text)),
        log_turn=lambda *a, **k: None,
    )

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert ("assistant", "corrected answer") in stored
    assert not any(text == "resumed answer" for _, text in stored)
    _, saved_history, _ = agent.saved_turns[-1]
    assert any(
        getattr(m, "content", "") == "corrected answer" for m in saved_history
    ), "history must carry the terminal answer"
    # ...and the user is TOLD, since the draft's tokens are already gone out.
    assert any("__jarvis_final__" in c for c in chunks)
    assert any("corrected answer" in c for c in chunks)


@pytest.mark.asyncio
async def test_resume_emits_no_correction_marker_when_nothing_changed(monkeypatch):
    """The marker means 'actually, this' -- an unchanged answer must not
    make every confirmed turn end with a redundant JSON frame."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent({
        "c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None},
    })
    agent._checkpointer = SimpleNamespace(get_tuple=lambda cfg: _checkpoint(
        response="resumed answer",  # exactly what _fake_stream yielded
    ))

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert not any("__jarvis_final__" in c for c in chunks)
    assert "".join(chunks) == "resumed answer"


@pytest.mark.asyncio
async def test_resume_falls_back_to_the_stream_without_a_checkpoint_response(monkeypatch):
    """An old checkpoint (or a failed read) must persist what the user
    actually saw rather than nothing -- same fallback chat_stream() takes."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent({
        "c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None},
    })
    agent._checkpointer = SimpleNamespace(get_tuple=lambda cfg: _checkpoint())
    stored: list[tuple] = []
    agent.memory = SimpleNamespace(
        store=lambda role, text, sid: stored.append((role, text)),
        log_turn=lambda *a, **k: None,
    )

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert ("assistant", "resumed answer") in stored
    assert not any("__jarvis_final__" in c for c in chunks)


@pytest.mark.asyncio
async def test_approved_external_write_buffers_and_discards_uncertain_model_text(monkeypatch):
    """Voice and token streams must never receive prose the receipt replaces."""
    uncertain = "E-postanın gerçekten gönderildiğinden emin değilim."

    async def _uncertain_stream(graph, command, config):
        yield uncertain

    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _uncertain_stream)
    agent = _FakeResumeAgent({
        "c1": {
            "config": {"configurable": {"thread_id": "s1-t1"}},
            "recorder": None,
            "result_binding_candidate": True,
        },
    })
    bound_row = {
        "tool": "gmail",
        "tool_call_id": "call-1",
        "action": "send",
        "ok": True,
        "authorization": "user_approved",
        "side_effect_type": "external_write",
        "confirmation_required": True,
    }
    agent._checkpointer = SimpleNamespace(get_tuple=lambda cfg: _checkpoint(
        user_query="E-postayı gönder",
        response=uncertain,
        language="tr",
        tool_execution_ledger=[bound_row],
    ))

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert json.loads(chunks[0]) == {
        "__jarvis_progress__": True,
        "phase": "finalizing_action_result",
    }
    assert all(uncertain not in chunk for chunk in chunks)
    assert "başarılı" in chunks[-1]
    assert not any("__jarvis_final__" in chunk for chunk in chunks)
    _, saved_history, _ = agent.saved_turns[-1]
    assert any("başarılı" in getattr(message, "content", "") for message in saved_history)


@pytest.mark.asyncio
async def test_approve_buffers_fail_safe_when_checkpoint_probe_errors(monkeypatch):
    """A transient pre-stream read failure must not expose a voice draft."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent({
        "c1": {
            "config": {"configurable": {"thread_id": "s1-t1"}},
            "recorder": None,
            "result_binding_candidate": True,
        },
    })

    def _unreadable_checkpoint(config):
        raise OSError("transient checkpoint read failure")

    agent._checkpointer = SimpleNamespace(get_tuple=_unreadable_checkpoint)

    chunks = [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert json.loads(chunks[0])["phase"] == "finalizing_action_result"
    assert chunks[1:] == ["resumed answer"]


def test_confirmation_registration_pins_external_write_buffering_eligibility():
    agent = _FakeResumeAgent({})

    JarvisAgent._register_pending_confirmation(
        agent,
        "c1",
        {"configurable": {"thread_id": "s1-t1"}},
        None,
        payload={
            "tools": [{
                "name": "gmail",
                "args": {
                    "action": "send", "to": "a@b.c", "subject": "s", "body": "b",
                },
            }],
        },
    )

    assert agent._pending_confirmations["c1"]["result_binding_candidate"] is True


@pytest.mark.asyncio
async def test_resume_does_not_store_a_blank_user_row(monkeypatch):
    """An old checkpoint with no user_query must store nothing for the user
    side rather than an empty row -- the guard the fix above needs."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _fake_stream)
    agent = _FakeResumeAgent({
        "c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None},
    })
    stored: list[tuple] = []
    agent.memory = SimpleNamespace(
        store=lambda role, text, sid: stored.append((role, text)),
        log_turn=lambda *a, **k: None,
    )

    [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]

    assert not any(role == "user" for role, _ in stored)
