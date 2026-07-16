"""jarvis/agent.py's background_turn() + jarvis/task_executor.py's caller --
Faz 5 of the GPT-5.6 review remediation plan (runtime/concurrency isolation).

Before this fix, TaskExecutor._run() called self._agent.chat() directly:
(a) it read/wrote self._history mid-flight, interleaving a background task's
    exchange into the live conversation transcript the user is looking at;
(b) it held _state_lock for the entire (potentially long) LLM/tool loop, so
    a long background task blocked every foreground chat()/chat_stream()
    call for as long as it ran.

background_turn() fixes both: an isolated LangGraph thread_id/message list
during the actual ainvoke() (no lock held, no self._history touched), then a
brief locked append of the result into the real self._history afterward --
same shared-state invariant _state_lock protects everywhere else (BUG-8),
just not held for the long part anymore.

Calls JarvisAgent.background_turn(fake_self, ...) unbound against a minimal
stand-in object -- constructing a real JarvisAgent is heavy (real LLM
provider setup, real graph build) and no existing test in this suite does
so; this isolates the method under test to exactly the attributes it reads/
writes.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphInterrupt

from jarvis.agent import ConfirmationRequired, JarvisAgent
from jarvis.config import Settings


class _FakeAgent:
    """Minimal stand-in exposing exactly what background_turn() touches."""

    def __init__(self):
        self._history = []
        self._turn = 0
        self.session_id = "sess1"
        self._state_lock = threading.Lock()
        self.settings = Settings(_env_file=None)
        self._env_block = ""
        self._context_builder = SimpleNamespace(
            build=lambda query, session_id=None: SimpleNamespace(
                memory_ctx="", entities_block="", past_sessions_block="",
                open_todos_block="", facts_block="", procedure_block="",
            )
        )
        self.saved_turns: list[tuple] = []
        self.session_store = SimpleNamespace(
            save_turn=lambda sid, hist, turn: self.saved_turns.append((sid, list(hist), turn))
        )
        self.stored_memories: list[tuple] = []
        self.memory = SimpleNamespace(
            store=lambda role, content, sid: self.stored_memories.append((role, content, sid))
        )
        self._graph = None
        self._mcp_connect_called = False
        # LlmTraceRecorder(usage=self.usage, ...) reads this in background_turn()
        # -- None is a valid value (the recorder just skips accounting).
        self.usage = None

    async def connect_mcp_tools(self):
        self._mcp_connect_called = True

    async def _acquire_state_lock(self):
        await asyncio.get_running_loop().run_in_executor(None, self._state_lock.acquire)


def _immediate_graph(response_text: str):
    async def ainvoke(state, config):
        return {"response": response_text, "messages": state["messages"]}
    return SimpleNamespace(ainvoke=ainvoke)


@pytest.mark.asyncio
async def test_background_turn_does_not_hold_lock_during_ainvoke():
    agent = _FakeAgent()
    invoke_started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_ainvoke(state, config):
        invoke_started.set()
        await proceed.wait()
        return {"response": "done", "messages": []}
    agent._graph = SimpleNamespace(ainvoke=slow_ainvoke)

    task = asyncio.create_task(JarvisAgent.background_turn(agent, "do deep research"))
    await invoke_started.wait()

    acquired = agent._state_lock.acquire(blocking=False)
    assert acquired, "background_turn must not hold _state_lock during the long ainvoke() call"
    agent._state_lock.release()
    assert agent._history == [], "self._history must be untouched while the task is still running"

    proceed.set()
    result = await task
    assert result == "done"


@pytest.mark.asyncio
async def test_background_turn_appends_result_as_message_pair_to_real_history():
    agent = _FakeAgent()
    agent._graph = _immediate_graph("The report is ready at data/reports/x.pdf.")

    result = await JarvisAgent.background_turn(agent, "generate the monthly report")

    assert result == "The report is ready at data/reports/x.pdf."
    assert len(agent._history) == 2
    assert isinstance(agent._history[0], HumanMessage)
    assert "generate the monthly report" in agent._history[0].content
    assert isinstance(agent._history[1], AIMessage)
    assert agent._history[1].content == "The report is ready at data/reports/x.pdf."
    assert agent.saved_turns, "session_store.save_turn must be called so this survives a reload"
    assert agent.stored_memories, "episodic memory must record the exchange"


@pytest.mark.asyncio
async def test_background_turn_uses_isolated_thread_id_not_main_session_turn():
    agent = _FakeAgent()
    seen_config = {}

    async def ainvoke(state, config):
        seen_config.update(config)
        return {"response": "ok", "messages": []}
    agent._graph = SimpleNamespace(ainvoke=ainvoke)

    await JarvisAgent.background_turn(agent, "a task")

    thread_id = seen_config["configurable"]["thread_id"]
    assert thread_id.startswith(f"{agent.session_id}-task-")
    assert thread_id != f"{agent.session_id}-t{agent._turn}"


@pytest.mark.asyncio
async def test_background_turn_raises_confirmation_required_on_interrupt():
    agent = _FakeAgent()
    fake_interrupt = SimpleNamespace(value={"tools": [{"name": "gmail"}]})

    async def interrupting_ainvoke(state, config):
        raise GraphInterrupt([fake_interrupt])
    agent._graph = SimpleNamespace(ainvoke=interrupting_ainvoke)

    with pytest.raises(ConfirmationRequired) as exc_info:
        await JarvisAgent.background_turn(agent, "send an email")

    assert exc_info.value.payload.get("tools") == [{"name": "gmail"}]
    assert agent._history == [], "an interrupted task must not be appended to real history"
    assert not agent.saved_turns


# ── task_executor.py's caller ────────────────────────────────────────────────

from jarvis.task_executor import TaskExecutor


class _StubBackgroundAgent:
    def __init__(self, *, result=None, raises=None):
        self._result = result
        self._raises = raises
        self.calls: list[str] = []

    async def background_turn(self, query, transport="task-async"):
        self.calls.append(query)
        if self._raises is not None:
            raise self._raises
        return self._result


def test_task_executor_calls_background_turn_not_chat():
    agent = _StubBackgroundAgent(result="All done.")
    executor = TaskExecutor(agent)
    task = executor.submit("write a report")

    for _ in range(50):
        if task.status in ("done", "failed"):
            break
        import time
        time.sleep(0.05)

    assert agent.calls == ["write a report"]
    assert task.status == "done"
    assert task.result_text == "All done."


def test_task_executor_reports_confirmation_required_without_crashing():
    agent = _StubBackgroundAgent(raises=ConfirmationRequired("id1", {"tools": [{"name": "shell_run"}]}))
    executor = TaskExecutor(agent)
    task = executor.submit("run a shell command")

    for _ in range(50):
        if task.status in ("done", "failed"):
            break
        import time
        time.sleep(0.05)

    assert task.status == "failed"
    assert "shell_run" in task.error
    assert "background" in task.error.lower()
