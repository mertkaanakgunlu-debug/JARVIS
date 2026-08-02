"""Which conversation a background/proactive turn belongs to — the GPT review's
P0, and the foreground-blocking fix next to it.

`background_turn()` already pinned `origin_session_id` at entry and used it for
the memory context, the working-set prompt block and the history append. The
RunnableConfig did not: `thread_id` and `conversation_id` were still read from
`self.session_id`, i.e. whichever conversation happened to be live when the
worker picked the task up. A task submitted in conversation A while the user
moved on to B therefore showed the model A's working set in its prompt and
handed every config-reading tool B's id. `chart_revise` would then edit B's
chart, or refuse with "bu nesne bu konuşmaya ait değil" about an object the
prompt had just described to it. That is the cross-conversation contamination
the Working Set is keyed to prevent, reopened on the background path -- and it
is the one that matters most before Faz 5, where the background path starts
creating calendar events.

The lock tests belong here for the same reason: a proactive check that freezes
the user's live conversation for a minute is the other way a background turn
reaches into a foreground one.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.agent import JarvisAgent

from tests.test_background_turn import _FakeAgent


def _config_capturing_graph(sink: dict):
    async def ainvoke(state, config):
        sink.update(config)
        return {"response": "ok", "messages": []}
    return SimpleNamespace(ainvoke=ainvoke)


# ── P0: the submitting conversation owns the turn ────────────────────────────

@pytest.mark.asyncio
async def test_background_config_follows_the_submitting_conversation():
    """THE P0. Submitted from A, agent has since switched to B: every
    config-reading tool must still be told A."""
    agent = _FakeAgent()
    agent.session_id = "conversation-B"          # the user moved on
    seen: dict = {}
    agent._graph = _config_capturing_graph(seen)

    await JarvisAgent.background_turn(
        agent, "finish the report", conversation_id="conversation-A"
    )

    configurable = seen["configurable"]
    assert configurable["conversation_id"] == "conversation-A"
    assert configurable["thread_id"].startswith("conversation-A-task-")
    assert "conversation-B" not in configurable["thread_id"]


@pytest.mark.asyncio
async def test_the_prompt_and_the_tool_config_name_the_same_conversation():
    """The specific incoherence: the working-set block was built for A while
    the tools were handed B, so the model was shown one chart and given the
    means to edit a different one."""
    agent = _FakeAgent()
    agent.session_id = "conversation-B"
    asked_for: list[str] = []
    agent.working_set = SimpleNamespace(
        list=lambda conversation: (asked_for.append(conversation), [])[1]
    )
    seen: dict = {}
    agent._graph = _config_capturing_graph(seen)

    await JarvisAgent.background_turn(
        agent, "draw it again", conversation_id="conversation-A"
    )

    assert asked_for == ["conversation-A"], "prompt block built for the wrong conversation"
    assert seen["configurable"]["conversation_id"] == asked_for[0], (
        "the prompt describes one conversation's objects while the tools mutate another's"
    )


@pytest.mark.asyncio
async def test_no_conversation_id_still_means_the_live_session():
    """Every pre-existing caller passes nothing; that must keep meaning
    'the current session', not empty."""
    agent = _FakeAgent()
    agent.session_id = "sess1"
    seen: dict = {}
    agent._graph = _config_capturing_graph(seen)

    await JarvisAgent.background_turn(agent, "a task")

    assert seen["configurable"]["conversation_id"] == "sess1"
    assert seen["configurable"]["thread_id"].startswith("sess1-task-")


# ── the proactive path must not freeze the foreground ────────────────────────

class _ProactiveAgent(_FakeAgent):
    """proactive_turn() reads a little less than background_turn()."""

    def __init__(self):
        super().__init__()
        self.settings.monitor_proactive_enabled = True


@pytest.mark.asyncio
async def test_proactive_turn_does_not_hold_the_lock_during_the_model_call():
    """Measured proactive turns run 20-80 s. chat()/chat_stream() wait on this
    same lock, so holding it across ainvoke() froze the user's live
    conversation for the whole check."""
    agent = _ProactiveAgent()
    invoke_started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_ainvoke(state, config):
        invoke_started.set()
        await proceed.wait()
        return {"response": "nothing to report", "messages": []}
    agent._graph = SimpleNamespace(ainvoke=slow_ainvoke)

    task = asyncio.create_task(
        JarvisAgent.proactive_turn(agent, "yeni mail geldi", source="email")
    )
    await invoke_started.wait()

    acquired = agent._state_lock.acquire(blocking=False)
    assert acquired, "proactive_turn must not hold _state_lock during the model call"
    agent._state_lock.release()

    proceed.set()
    outcome = await task
    assert outcome.kind == "response"


@pytest.mark.asyncio
async def test_proactive_turn_releases_the_lock_when_the_model_raises():
    agent = _ProactiveAgent()

    async def boom(state, config):
        raise RuntimeError("provider down")
    agent._graph = SimpleNamespace(ainvoke=boom)

    outcome = await JarvisAgent.proactive_turn(agent, "yeni mail", source="email")

    assert outcome.kind == "none"
    assert agent._state_lock.acquire(blocking=False), "lock leaked on the error path"
    agent._state_lock.release()


@pytest.mark.asyncio
async def test_proactive_turn_still_leaves_the_conversation_untouched():
    """The isolation that justified dropping the lock in the first place."""
    agent = _ProactiveAgent()
    agent._graph = _config_capturing_graph({})

    await JarvisAgent.proactive_turn(agent, "yeni mail", source="email")

    assert agent._history == []
    assert agent.saved_turns == []
    assert agent.stored_memories == []
