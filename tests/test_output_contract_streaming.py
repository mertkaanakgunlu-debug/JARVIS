"""What a contracted turn is allowed to put on the wire.

A completion repair does not correct an answer, it REPLACES one: the user has
already been streamed "which format would you like?" and is about to be given
a chart instead. The existing __jarvis_final__ marker cannot cover that,
because voice deliberately swallows the marker -- so the spoken user would
hear only the discarded draft while a chart appeared behind it.

Hence: a turn that carries a required output emits no text until the graph
has finished. These tests pin that it holds, that it stays narrow (every
other turn streams exactly as before), and that it never swallows a
confirmation prompt -- which would deadlock the turn it is trying to protect.
"""
from __future__ import annotations

import asyncio
import json
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import jarvis.agent as agent_mod
from jarvis.agent import (
    JarvisAgent,
    finalize_terminal_response,
    strip_completion_contract_marker,
)
from jarvis.config import Settings
from jarvis.execution.output_contract import REPAIR_DIRECTIVE

CHART = [{"kind": "chart", "operation": "create"}]


class _FakeResumeAgent:
    """Exactly what resume_and_stream() touches -- the same stand-in shape
    test_confirmation_resume_trace.py uses, kept local so a change there
    cannot quietly alter what this file is asserting."""

    def __init__(self, mode="enforce", channel_values=None):
        self._state_lock = threading.Lock()
        self._pending_confirmations = {
            "c1": {"config": {"configurable": {"thread_id": "s1-t1"}}, "recorder": None},
        }
        self._history = []
        self._turn = 1
        self.session_id = "s1"
        self._graph = SimpleNamespace(
            aget_state=AsyncMock(return_value=SimpleNamespace(interrupts=()))
        )
        self._last_turn_trace = {}
        self._record_turn_trace = types.MethodType(JarvisAgent._record_turn_trace, self)
        self.settings = Settings(_env_file=None, required_outputs_mode=mode)
        self._contract_buffered = types.MethodType(JarvisAgent._contract_buffered, self)
        values = {"tool_execution_ledger": [], **(channel_values or {})}
        self._checkpointer = SimpleNamespace(
            get_tuple=lambda cfg: SimpleNamespace(checkpoint={"channel_values": values})
        )
        self.saved_turns: list[tuple] = []
        self.session_store = SimpleNamespace(
            save_turn=lambda sid, hist, turn: self.saved_turns.append((sid, list(hist), turn)),
        )
        self.stored: list[tuple] = []
        self.memory = SimpleNamespace(
            store=lambda role, text, sid: self.stored.append((role, text)),
            log_turn=lambda *a, **k: None,
        )

    async def _acquire_state_lock(self):
        await asyncio.get_running_loop().run_in_executor(None, self._state_lock.acquire)

    def _schedule_memory_extraction(self, user_text, response):
        pass

    def _register_pending_confirmation(self, conf_id, config, recorder):
        self._pending_confirmations[conf_id] = {"config": config, "recorder": recorder}

    async def _pending_interrupt_payload(self, config):
        return await JarvisAgent._pending_interrupt_payload(self, config)


async def _draft_stream(graph, command, config):
    yield "Hangi "
    yield "formatta istersiniz?"


async def _resume(agent):
    return [c async for c in JarvisAgent.resume_and_stream(agent, "c1", "approve")]


# ── the buffer itself ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_contracted_turn_emits_the_verified_answer_and_nothing_else(monkeypatch):
    """The draft's tokens never reach the wire; the answer that does is the
    one the graph finished with, exactly once."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _draft_stream)
    agent = _FakeResumeAgent(channel_values={
        "required_outputs": CHART, "response": "İşte grafiğiniz: chart.png",
    })

    chunks = await _resume(agent)

    assert chunks == ["İşte grafiğiniz: chart.png"]
    assert not any("Hangi formatta" in c for c in chunks), "the discarded draft leaked"
    assert not any("__jarvis_final__" in c for c in chunks), (
        "there is no earlier draft to correct -- a marker would be a correction "
        "of something the user never saw"
    )


@pytest.mark.asyncio
async def test_an_uncontracted_turn_streams_exactly_as_before(monkeypatch):
    """The buffer must stay narrow. Every turn without a required output --
    which is nearly all of them -- keeps its token-by-token stream."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _draft_stream)
    agent = _FakeResumeAgent(channel_values={"required_outputs": []})

    assert await _resume(agent) == ["Hangi ", "formatta istersiniz?"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "shadow"])
async def test_shadow_and_off_never_buffer(monkeypatch, mode):
    """Buffering is a user-visible behaviour change, so it belongs to enforce
    alone -- a shadow rollout that altered the streaming experience would not
    be a shadow of anything."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _draft_stream)
    agent = _FakeResumeAgent(mode=mode, channel_values={"required_outputs": CHART})

    assert await _resume(agent) == ["Hangi ", "formatta istersiniz?"]


@pytest.mark.asyncio
async def test_an_unreadable_checkpoint_falls_back_to_streaming(monkeypatch):
    """Failing open here is the safe direction: the worst case is today's
    behaviour, whereas failing closed would silently stop streaming turns
    nobody asked to buffer."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _draft_stream)
    agent = _FakeResumeAgent()
    agent._checkpointer = SimpleNamespace(
        get_tuple=lambda cfg: (_ for _ in ()).throw(RuntimeError("db locked"))
    )

    assert await _resume(agent) == ["Hangi ", "formatta istersiniz?"]


@pytest.mark.asyncio
async def test_a_confirmation_prompt_is_never_held_back(monkeypatch):
    """The one thing that must NOT be buffered. The graph cannot finish until
    the user answers, and the user cannot answer a prompt that is waiting for
    the graph to finish -- buffering the control frame would deadlock exactly
    the turns this feature exists to improve."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _draft_stream)
    agent = _FakeResumeAgent(channel_values={
        "required_outputs": CHART, "response": "unreachable",
    })
    agent._graph = SimpleNamespace(aget_state=AsyncMock(
        return_value=SimpleNamespace(interrupts=(SimpleNamespace(value={"tool": "plot_data"}),))
    ))

    chunks = await _resume(agent)

    frames = [json.loads(c) for c in chunks if c.startswith("{")]
    assert any(f.get("__jarvis_confirm__") for f in frames)
    assert not any("unreachable" in c for c in chunks), (
        "the turn is not over -- its answer must not be emitted yet"
    )


# ── the one canonical sanitiser ────────────────────────────────────────────

def test_an_echoed_repair_directive_is_stripped_from_the_answer():
    """The model copying a system-generated marker into its own answer is not
    hypothetical here: it happened on 2026-07-30 with the tool-execution
    summary. The directive is a whole block, so a single-line strip would
    leave the rest of it in the user's answer."""
    echoed = (
        "[Completion Contract]\n"
        "The user explicitly asked for a chart and no chart was produced this turn.\n"
        "Use your one remaining repair attempt to produce one.\n"
        "\n"
        "Grafiği çizdim efendim."
    )
    assert strip_completion_contract_marker(echoed) == "Grafiği çizdim efendim."


def test_the_sanitiser_leaves_an_ordinary_answer_alone():
    for text in ["Grafiği çizdim.", "", "   ", "Bu bir [Completion] değil"]:
        assert finalize_terminal_response(text) == text


def test_both_markers_are_handled_by_the_one_entry_point():
    """chat() cleaned one marker, the other three surfaces cleaned nothing.
    One function called in four places is the only version of this that stays
    true as markers are added."""
    assert finalize_terminal_response(
        "[Tool execution summary: plot_data ok]Grafik hazır."
    ) == "Grafik hazır."
    assert finalize_terminal_response(
        f"{REPAIR_DIRECTIVE}\nGrafik hazır."
    ) == "Grafik hazır."


def test_a_paraphrased_directive_is_left_alone():
    """Only the directive's OWN lines are removed. A model that reworded the
    instruction is producing prose, and prose that merely resembles a marker
    is still the model's answer -- deleting it would be the sanitiser
    inventing an edit, a worse failure than leaving a stray line in.

    This is also why the strip matches line-for-line rather than "everything
    down to the next blank line": the directive has no blank line ending it,
    so the block form ate the answer whole."""
    paraphrase = "[Completion Contract]\nI should use my one attempt.\nGrafik hazır."
    assert strip_completion_contract_marker(paraphrase) == (
        "I should use my one attempt.\nGrafik hazır."
    )


@pytest.mark.asyncio
async def test_the_persisted_answer_is_sanitised_too(monkeypatch):
    """History and episodic memory feed the NEXT turn's context, so a marker
    that survives into them teaches the model the format it should not be
    imitating in the first place."""
    monkeypatch.setattr(agent_mod, "graph_stream_to_text", _draft_stream)
    agent = _FakeResumeAgent(channel_values={
        "required_outputs": CHART,
        "response": f"{REPAIR_DIRECTIVE}\nGrafik hazır.",
    })

    chunks = await _resume(agent)

    assert chunks == ["Grafik hazır."]
    assert ("assistant", "Grafik hazır.") in agent.stored
    _, saved_history, _ = agent.saved_turns[-1]
    assert all(
        "[Completion Contract]" not in getattr(m, "content", "") for m in saved_history
    )
