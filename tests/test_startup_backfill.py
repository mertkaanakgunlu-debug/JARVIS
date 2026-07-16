"""jarvis/agent.py's run_startup_backfill()/_schedule_summary_backfill() --
Faz 7 of the GPT-5.6 review remediation plan (verdict #17, CONFIRMED).

_schedule_summary_backfill() was only ever called from JarvisAgent.__init__,
which both real entry points (cli.py, api.py) run *before* asyncio.run()/
uvicorn start their event loop -- so its own "no event loop yet -> no-op"
guard always fired and summary backfill silently never ran. Faz 7 adds
run_startup_backfill(), called from cli.py's _run_loop()/_run_voice_loop()
and api.py's lifespan() once their real loop is actually up.

Calls JarvisAgent.run_startup_backfill(fake_self) unbound against a minimal
stand-in, same approach as test_background_turn.py -- constructing a real
JarvisAgent is heavy and not this suite's pattern.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jarvis.agent import JarvisAgent


def _fake_agent(pending):
    fake = SimpleNamespace()
    fake._bg_tasks = set()
    fake.session_store = SimpleNamespace(
        sessions_needing_summary=lambda: pending,
        load_full_history=lambda sid: [{"role": "user", "content": "hi"}] if pending else [],
        set_summary=lambda *a, **k: None,
    )
    fake.memory = SimpleNamespace(store_summary=lambda *a, **k: None)
    fake._schedule_summary_backfill = lambda: JarvisAgent._schedule_summary_backfill(fake)
    return fake


@pytest.mark.asyncio
async def test_run_startup_backfill_schedules_work_when_loop_running_and_pending_exists():
    fake = _fake_agent(pending=[{"id": "s1", "topic_hint": "budget", "last_active": None}])

    JarvisAgent.run_startup_backfill(fake)

    assert len(fake._bg_tasks) == 1, "a background task must be scheduled once a real loop is running"
    await asyncio.sleep(0)  # let the fire-and-forget task get a turn, then clean up
    for t in list(fake._bg_tasks):
        t.cancel()


@pytest.mark.asyncio
async def test_run_startup_backfill_is_noop_when_nothing_pending():
    fake = _fake_agent(pending=[])

    JarvisAgent.run_startup_backfill(fake)

    assert fake._bg_tasks == set()


def test_schedule_summary_backfill_noops_without_a_running_loop():
    """The __init__-time call (before asyncio.run()/uvicorn starts) must not
    raise RuntimeError -- confirms the pre-existing guard this fix builds on
    top of still holds; run_startup_backfill()'s value is calling this again
    later, once a loop genuinely exists."""
    fake = _fake_agent(pending=[{"id": "s1", "topic_hint": None, "last_active": None}])

    JarvisAgent._schedule_summary_backfill(fake)  # no event loop in this sync test

    assert fake._bg_tasks == set()
