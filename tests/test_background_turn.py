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
    """Minimal stand-in exposing exactly what background_turn() touches.

    Helper METHODS are borrowed from the real class rather than re-stubbed
    (see `_working_set_turn` below). That is not tidiness — it is the fix for a
    failure mode this stub produced live: `background_turn` grew a call to a
    new helper, the duck-typed stub did not have it, and the AttributeError
    fired before `invoke_started` was set, so a test that waits on that event
    hung FOREVER instead of failing. A hang is the worst way for a suite to
    report a missing attribute, and borrowing the method means a future helper
    exercises the real code here instead of silently diverging.
    """

    # Bound to the instance at call time, so `self` is this stub.
    _working_set_turn = JarvisAgent._working_set_turn
    # Post-MVP Faz 6, and the docstring above predicted it exactly:
    # background_turn grew a call to _output_contract_state and this stub had
    # to grow with it. Borrowed, not stubbed, so the contract's "background
    # work is the USER's work" wiring is exercised here rather than faked.
    _output_contract_state = JarvisAgent._output_contract_state
    # Omitting this did not produce a failure -- it produced the HANG this
    # class's docstring warns about, exactly as described: AttributeError
    # fires before invoke_started is set, and the waiting test never wakes up.
    # Twice now, which is what borrowing rather than stubbing is supposed to
    # prevent -- and why the waits below are bounded (see _await_event).
    _recursion_limit_for = JarvisAgent._recursion_limit_for

    def __init__(self):
        self._history = []
        self._turn = 0
        self.session_id = "sess1"
        self._state_lock = threading.Lock()
        self.settings = Settings(_env_file=None)
        # Plain attribute, NOT _env_static: this is a standalone duck-type, not a
        # JarvisAgent instance, so it does not inherit the _env_block property
        # (which composes static paths with a live clock -- see _build_now_block).
        self._env_block = ""
        self._context_builder = SimpleNamespace(
            build=lambda query, session_id=None: SimpleNamespace(
                memory_ctx="", entities_block="", past_sessions_block="",
                open_todos_block="", facts_block="", procedure_block="",
            )
        )
        self.saved_turns: list[tuple] = []
        # Patch 1.1: background_turn's origin-session path also reads the
        # origin's stored history/turn counter when the live session moved on.
        self.origin_store_history: list = []
        self.origin_last_turn_idx = 0
        self.session_store = SimpleNamespace(
            save_turn=lambda sid, hist, turn: self.saved_turns.append((sid, list(hist), turn)),
            load_history=lambda sid, limit=20: list(self.origin_store_history),
            last_turn_idx=lambda sid: self.origin_last_turn_idx,
        )
        self.stored_memories: list[tuple] = []
        self.memory = SimpleNamespace(
            store=lambda role, content, sid: self.stored_memories.append((role, content, sid))
        )
        self._graph = None
        self._mcp_connect_called = False
        # Post-MVP Faz 4: background_turn now reads the submitting
        # conversation's working set. An empty one is the right stand-in --
        # these tests are about lock/history/session semantics, not charts.
        self.working_set = SimpleNamespace(list=lambda _conversation: [])
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


# pytest-timeout is not installed in this project, so a coroutine that never
# completes hangs the whole suite with no output -- which is exactly what
# happened twice while this module's stand-in lagged behind background_turn's
# growing set of collaborators. The AttributeError that should have failed
# these tests instead fired inside the task, before `invoke_started` was set,
# leaving the awaits below waiting forever.
#
# So the waits are bounded locally, and the timeout message says where to
# look: a hang here is nearly always a method background_turn() now calls that
# _FakeAgent has not borrowed yet.
_EVENT_TIMEOUT_S = 10


async def _await_event(event: asyncio.Event, what: str) -> None:
    try:
        await asyncio.wait_for(event.wait(), timeout=_EVENT_TIMEOUT_S)
    except asyncio.TimeoutError:  # pragma: no cover -- only on a real hang
        raise AssertionError(
            f"{what} never happened within {_EVENT_TIMEOUT_S}s. background_turn() "
            "most likely raised inside the task -- check that _FakeAgent borrows "
            "every JarvisAgent helper it now calls."
        ) from None


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
    await _await_event(invoke_started, "the background graph invocation")

    acquired = agent._state_lock.acquire(blocking=False)
    assert acquired, "background_turn must not hold _state_lock during the long ainvoke() call"
    agent._state_lock.release()
    assert agent._history == [], "self._history must be untouched while the task is still running"

    proceed.set()
    result = await asyncio.wait_for(task, timeout=_EVENT_TIMEOUT_S)
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
async def test_background_result_lands_in_origin_session_after_midflight_reset():
    """Patch 1.1 (session-contamination race): the user /reset (or switched
    sessions) while the task ran. The result must be persisted into the
    session that SUBMITTED the task -- in a fresh turn_idx bucket past that
    session's last save -- and the LIVE conversation must stay completely
    untouched (the task-completion notification is the only thing the new
    session sees)."""
    agent = _FakeAgent()
    agent.origin_store_history = [HumanMessage(content="earlier turn")]
    agent.origin_last_turn_idx = 3
    invoke_started = asyncio.Event()
    proceed = asyncio.Event()

    async def slow_ainvoke(state, config):
        invoke_started.set()
        await proceed.wait()
        return {"response": "done late", "messages": []}
    agent._graph = SimpleNamespace(ainvoke=slow_ainvoke)

    task = asyncio.create_task(JarvisAgent.background_turn(agent, "long research job"))
    await _await_event(invoke_started, "the background graph invocation")

    # Mid-flight reset: a new session becomes live.
    agent.session_id = "sess2-after-reset"
    agent._history = []
    agent._turn = 0

    proceed.set()
    result = await asyncio.wait_for(task, timeout=_EVENT_TIMEOUT_S)

    assert result == "done late"
    assert agent._history == [], "the live (post-reset) conversation must stay untouched"
    assert len(agent.saved_turns) == 1
    sid, hist, turn = agent.saved_turns[0]
    assert sid == "sess1", "the result must be persisted into the ORIGIN session"
    assert turn == 4, "a fresh bucket past the origin's last turn_idx, never bucket-0 of the new session"
    assert any("long research job" in getattr(m, "content", "") for m in hist)
    assert hist[-1].content == "done late"
    assert all(sid == "sess1" for _, _, sid in agent.stored_memories), \
        "episodic memory must tag the origin session too"


@pytest.mark.asyncio
async def test_background_turn_same_session_keeps_existing_behavior():
    """No reset mid-task: the pair still lands in the live history and is
    saved under the (unchanged) live session id, exactly as before."""
    agent = _FakeAgent()
    agent._turn = 2
    agent._graph = _immediate_graph("quick result")

    await JarvisAgent.background_turn(agent, "small job")

    assert len(agent._history) == 2
    sid, _hist, turn = agent.saved_turns[0]
    assert sid == "sess1" and turn == 2


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
        self.conversations: list[str] = []

    async def background_turn(self, query, transport="task-async", conversation_id=""):
        # conversation_id: Post-MVP Faz 2.75 (Paket B). Recorded, not just
        # accepted -- a stub that swallowed it would let the executor stop
        # passing it without any test noticing.
        self.calls.append(query)
        self.conversations.append(conversation_id)
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


def test_the_executor_hands_the_submitting_conversation_to_the_agent():
    """Paket B: a task can sit queued while other clients talk, so the origin
    conversation has to travel with the task rather than be read off the shared
    agent when a worker finally picks it up."""
    import time

    agent = _StubBackgroundAgent(result="ok")
    executor = TaskExecutor(agent)
    task = executor.submit("uzun bir iş", "conv-A")
    for _ in range(50):
        if task.status in ("done", "failed"):
            break
        time.sleep(0.05)

    assert task.status == "done"
    assert agent.conversations == ["conv-A"]
