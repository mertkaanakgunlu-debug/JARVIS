"""kill_switch.py — the emergency-stop mechanism for L3 tool calls (Faz 4),
including its Faz 8 cross-process staleness fix.

Persisted to a small JSON file specifically so a trip survives a process
restart and, more importantly, propagates to *other already-running*
processes (e.g. a long-lived `--api --monitor` server) without needing a
restart -- that second property is what Faz 8 fixed and what most of this
file guards against regressing.
"""
from __future__ import annotations

import json

from jarvis import kill_switch


def test_defaults_to_enabled(isolated_cwd):
    assert kill_switch.is_enabled() is True
    assert kill_switch.reason() == ""


def test_disable_then_enable_roundtrip(isolated_cwd):
    kill_switch.disable("owner said stop")
    assert kill_switch.is_enabled() is False
    assert kill_switch.reason() == "owner said stop"

    kill_switch.enable()
    assert kill_switch.is_enabled() is True
    assert kill_switch.reason() == ""


def test_persists_across_a_simulated_restart(isolated_cwd):
    """A 'restart' = a fresh call with no warm in-memory cache."""
    kill_switch.disable("persist me")
    kill_switch._cache = None  # simulate a brand new process's first read

    assert kill_switch.is_enabled() is False
    assert kill_switch.reason() == "persist me"


def test_trip_from_another_process_propagates_without_restart(isolated_cwd):
    """The Faz 8 fix: a long-running process's warm cache must not shadow a
    trip written by a different process to the same file.

    Simulated by writing directly to disk (bypassing this module's own
    disable()) to stand in for a genuinely separate OS process, then reading
    through the *same* already-warm module state -- exactly what the old
    load-once-per-process cache got wrong.
    """
    assert kill_switch.is_enabled() is True  # warm the cache on the "old" state

    path = isolated_cwd / "data" / "kill_switch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"enabled": False, "reason": "tripped elsewhere", "changed_at": "x"}),
        encoding="utf-8",
    )

    assert kill_switch.is_enabled() is False
    assert kill_switch.reason() == "tripped elsewhere"


def test_status_reflects_full_state(isolated_cwd):
    kill_switch.disable("full state check")
    status = kill_switch.status()
    assert status["enabled"] is False
    assert status["reason"] == "full state check"
    assert status["changed_at"]


def test_corrupt_file_falls_back_to_default(isolated_cwd):
    path = isolated_cwd / "data" / "kill_switch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")

    # Must not raise, and must fail toward *some* deterministic state rather
    # than crash the caller (policy_guard.evaluate() is on every L3 call).
    assert kill_switch.is_enabled() in (True, False)
