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


def test_bom_prefixed_state_file_is_still_read(isolated_cwd):
    """Live incident (2026-07-18 A/B harness): PowerShell 5.1's
    `Out-File -Encoding utf8` writes UTF-8 WITH a BOM. The loader read with
    plain utf-8, json.loads() choked on the BOM, and the silent fallback
    (stale cache / default) meant an external writer's TRIP could be
    invisible -- the emergency stop's worst possible failure mode -- and an
    external re-arm could leave a stale trip vetoing everything. Both
    directions must survive a BOM.
    """
    path = isolated_cwd / "data" / "kill_switch.json"
    path.parent.mkdir(parents=True, exist_ok=True)

    # A BOM'd TRIP must actually trip (safety-critical direction).
    path.write_text(
        json.dumps({"enabled": False, "reason": "bom trip", "changed_at": "x"}),
        encoding="utf-8-sig",
    )
    assert kill_switch.is_enabled() is False
    assert kill_switch.reason() == "bom trip"

    # A BOM'd RE-ARM must clear it even through a warm cache holding the trip.
    path.write_text(
        json.dumps({"enabled": True, "reason": "", "changed_at": "y"}),
        encoding="utf-8-sig",
    )
    assert kill_switch.is_enabled() is True
