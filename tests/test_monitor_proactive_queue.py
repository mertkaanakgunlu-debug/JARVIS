"""The monitor's proactive check must not stall its own poll loop.

`_maybe_proactive()` ran `asyncio.run(agent.proactive_turn(...))` inline on the
monitor's single polling thread. Measured proactive turns take 20-80 s, and for
that whole time the calendar, scheduler, todo, finance and GCP checks -- all
driven from the same loop -- simply did not run. A reminder that exists to fire
on time was blocked by a background question nobody asked.

It is now handed to a bounded queue drained by a dedicated worker. Bounded
because a backlog of stale "is this worth surfacing?" questions has no value,
and a dropped one is logged rather than silently discarded.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from jarvis.config import Settings
from jarvis.monitor import JarvisMonitor


class _SlowAgent:
    """proactive_turn() that blocks until released."""

    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[str] = []

    async def proactive_turn(self, prompt, *, source):
        self.calls.append(source)
        self.started.set()
        self.release.wait(timeout=10)
        return SimpleNamespace(kind="none", text="", tools=[])


@pytest.fixture
def settings():
    s = Settings(_env_file=None)
    s.monitor_proactive_enabled = True
    s.monitor_proactive_min_gap_sec = 0     # throttle is not what these test
    return s


def _monitor(settings, agent):
    m = JarvisMonitor(settings, agent=agent)
    return m


def test_maybe_proactive_returns_before_the_model_does(settings):
    """THE regression: the poll loop must keep its cadence."""
    agent = _SlowAgent()
    monitor = _monitor(settings, agent)
    try:
        started = time.monotonic()
        monitor._maybe_proactive("yeni mail geldi", source="email")
        elapsed = time.monotonic() - started

        assert elapsed < 1.0, f"_maybe_proactive blocked for {elapsed:.1f}s"
        assert agent.started.wait(timeout=5), "the worker never picked the job up"
    finally:
        agent.release.set()
        monitor.stop()


def test_the_poll_loop_keeps_running_while_a_check_is_in_flight(settings):
    """A second, different source must not wait behind the first."""
    agent = _SlowAgent()
    monitor = _monitor(settings, agent)
    try:
        monitor._maybe_proactive("mail", source="email")
        assert agent.started.wait(timeout=5)

        started = time.monotonic()
        monitor._maybe_proactive("takvim", source="calendar")
        assert time.monotonic() - started < 1.0, "enqueue blocked on the in-flight call"
    finally:
        agent.release.set()
        monitor.stop()


def test_a_full_queue_drops_rather_than_growing(settings, caplog):
    """Bounded by construction. The drop is logged -- silent truncation is what
    makes 'why did nothing fire' unanswerable."""
    agent = _SlowAgent()
    monitor = _monitor(settings, agent)
    try:
        for i in range(40):
            monitor._maybe_proactive(f"olay {i}", source="email")
        assert monitor._proactive_queue.qsize() <= 4
        assert any("dropped" in r.message or "dropped" in r.getMessage()
                   for r in caplog.records) or monitor._proactive_queue.full()
    finally:
        agent.release.set()
        monitor.stop()


def test_a_disabled_monitor_enqueues_nothing():
    s = Settings(_env_file=None)
    s.monitor_proactive_enabled = False
    agent = _SlowAgent()
    monitor = JarvisMonitor(s, agent=agent)
    try:
        monitor._maybe_proactive("mail", source="email")
        assert monitor._proactive_queue.qsize() == 0
        assert monitor._proactive_thread is None
    finally:
        monitor.stop()


def test_no_agent_enqueues_nothing(settings):
    monitor = JarvisMonitor(settings, agent=None)
    try:
        monitor._maybe_proactive("mail", source="email")
        assert monitor._proactive_queue.qsize() == 0
    finally:
        monitor.stop()


def test_a_failing_check_does_not_kill_the_worker(settings):
    """The worker outlives one bad call -- the next event still gets looked at."""
    seen: list[str] = []

    class _Flaky:
        async def proactive_turn(self, prompt, *, source):
            seen.append(source)
            if len(seen) == 1:
                raise RuntimeError("provider down")
            return SimpleNamespace(kind="none", text="", tools=[])

    monitor = _monitor(settings, _Flaky())
    try:
        monitor._maybe_proactive("bir", source="email")
        monitor._maybe_proactive("iki", source="calendar")
        deadline = time.monotonic() + 5
        while len(seen) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert seen == ["email", "calendar"]
    finally:
        monitor.stop()
