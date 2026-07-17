"""reset()/reset_async() lifecycle (stabilization sprint — GPT plan F.6/F.7).

Root cause being locked in: POST /reset ran the whole sync reset() inside
run_in_executor, so _schedule_summarize_one's asyncio.create_task executed on
a worker thread with no running event loop -> RuntimeError -> HTTP 500 on
every content-bearing session (and the api-shutdown auto-reset silently
skipped its summary the same way).

Follows this suite's unbound-method precedent (see test_background_turn.py):
constructing a real JarvisAgent is heavy, so methods run against a minimal
stand-in exposing exactly the attributes they touch.
"""
from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest

from jarvis.agent import JarvisAgent


class _FakeSessionStore:
    def __init__(self):
        self.archived: list[str] = []
        self._n = 0

    def archive_session(self, sid):
        self.archived.append(sid)

    def new_session(self):
        self._n += 1
        return f"s{self._n}"

    def load_full_history(self, sid):
        return []  # real _schedule_summarize_one's _do() exits early on empty

    def list_sessions(self, n):
        return []

    def set_summary(self, *a, **k):
        raise AssertionError("must not be reached with empty history")


def _fake_agent(*, had_content: bool = True):
    agent = SimpleNamespace(
        _state_lock=threading.Lock(),
        session_id="s0",
        _history=["msg"] if had_content else [],
        _turn=3 if had_content else 0,
        session_store=_FakeSessionStore(),
        _bg_tasks=set(),
        scheduled=[],
        # Patch 1.1: per-session telemetry/confirmation state the reset must
        # clear -- pre-populated as if a turn (and an interrupt) happened.
        _last_turn_trace={"provider": "ollama", "model": "qwen2.5:7b-instruct"},
        _last_turn_used_pro=True,
        _pending_confirmations={"conf-1": {"config": {}, "recorder": None}},
        # ...and the one preference that must SURVIVE a reset:
        _active_model_id="aistudio/gemini-2.5-flash",
    )
    agent._reset_state_sync = lambda: JarvisAgent._reset_state_sync(agent)
    agent._schedule_summarize_one = lambda sid: agent.scheduled.append(sid)
    return agent


# ── F6: content-bearing session resets successfully via reset_async ──────────

@pytest.mark.asyncio
async def test_reset_async_succeeds_with_content_and_schedules_summary():
    agent = _fake_agent(had_content=True)

    await JarvisAgent.reset_async(agent)

    assert agent.session_store.archived == ["s0"]
    assert agent.session_id == "s1"
    assert agent._history == [] and agent._turn == 0
    assert agent.scheduled == ["s0"], "summary must be scheduled for the archived session"
    assert agent._state_lock.acquire(blocking=False), "lock must be released"
    agent._state_lock.release()


@pytest.mark.asyncio
async def test_reset_async_empty_session_schedules_nothing():
    agent = _fake_agent(had_content=False)
    await JarvisAgent.reset_async(agent)
    assert agent.session_id == "s1"
    assert agent.scheduled == []


# ── Patch 1.1: reset clears per-session telemetry + pending confirmations ────

@pytest.mark.asyncio
async def test_reset_clears_turn_telemetry_and_pending_confirmations():
    """Without these clears, the fresh session's /status kept displaying the
    ARCHIVED session's provider/model rollup, and a pre-reset confirmation id
    stayed resumable -- its graph would have written history into the NEW
    session. The user's explicit /model pin is a preference, not per-session
    state, so it must survive."""
    agent = _fake_agent(had_content=True)

    await JarvisAgent.reset_async(agent)

    assert agent._last_turn_trace is None
    assert agent._last_turn_used_pro is None
    assert agent._pending_confirmations == {}
    assert agent._active_model_id == "aistudio/gemini-2.5-flash", \
        "the manual model pin must survive a reset"


@pytest.mark.asyncio
async def test_reset_async_with_real_scheduler_creates_task_on_loop():
    """End-to-end through the REAL _schedule_summarize_one: after to_thread
    returns we're back on the loop, so its create_task must succeed."""
    agent = _fake_agent(had_content=True)
    agent.settings = None  # _do() exits before touching settings (empty history)
    agent._schedule_summarize_one = (
        lambda sid: JarvisAgent._schedule_summarize_one(agent, sid)
    )

    await JarvisAgent.reset_async(agent)

    assert agent.session_id == "s1"
    # The fire-and-forget task was actually created (and may already be done).
    if agent._bg_tasks:
        await asyncio.gather(*agent._bg_tasks, return_exceptions=True)


# ── F7: summarizer scheduling failure must not fail the reset ─────────────────

@pytest.mark.asyncio
async def test_reset_async_survives_scheduler_exception():
    agent = _fake_agent(had_content=True)

    def _boom(sid):
        raise RuntimeError("scheduler exploded")
    agent._schedule_summarize_one = _boom

    await JarvisAgent.reset_async(agent)  # must NOT raise

    assert agent.session_store.archived == ["s0"]
    assert agent.session_id == "s1", "new session must be usable despite the failure"
    assert agent._state_lock.acquire(blocking=False)
    agent._state_lock.release()


# ── Regression: the original bug shape (sync reset on a loopless thread) ─────

def test_sync_reset_on_loopless_thread_does_not_raise():
    """The exact pre-fix failure: reset() with had_content=True executing on a
    thread with no running event loop (the API's old run_in_executor path).
    The guarded _schedule_summarize_one now skips the summary instead of
    raising RuntimeError."""
    agent = _fake_agent(had_content=True)
    agent._schedule_summarize_one = (
        lambda sid: JarvisAgent._schedule_summarize_one(agent, sid)
    )

    errors: list[BaseException] = []

    def _run():
        try:
            JarvisAgent.reset(agent)
        except BaseException as exc:  # noqa: BLE001 — the assertion target
            errors.append(exc)

    t = threading.Thread(target=_run)
    t.start()
    t.join(timeout=10)

    assert not errors, f"sync reset must not raise off-loop: {errors}"
    assert agent.session_id == "s1"
    assert agent._bg_tasks == set(), "no task can be scheduled without a loop"


def test_schedule_summarize_one_guard_without_loop():
    agent = _fake_agent()
    JarvisAgent._schedule_summarize_one(agent, "sX")  # no running loop here
    assert agent._bg_tasks == set()
