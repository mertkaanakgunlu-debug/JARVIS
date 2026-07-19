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
import logging
import threading
import time
from pathlib import Path

from jarvis import kill_switch


def _state_path(root):
    """data/kill_switch.json under the isolated cwd, parent dirs ensured."""
    path = root / "data" / "kill_switch.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


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


def test_corrupt_existing_file_fails_safe_tripped(isolated_cwd, caplog):
    """2026-07-19 review hardening: a state file that EXISTS but cannot be
    parsed is a fail-CLOSED condition — the emergency stop must not quietly
    report "armed-off" (the old behavior fell back to the stale cache or the
    armed default, exactly the BOM incident's silent directions). Must not
    raise either: policy_guard.evaluate() is on every L3 call.
    """
    _state_path(isolated_cwd).write_text("{not valid json", encoding="utf-8")

    with caplog.at_level(logging.CRITICAL, logger="jarvis.kill_switch"):
        assert kill_switch.is_enabled() is False
    assert "unreadable" in kill_switch.reason()
    assert any("failing SAFE" in r.message for r in caplog.records)


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


# ── Failure-mode matrix (2026-07-19 review hardening) ──────────────────────────


def test_truncated_json_fails_safe(isolated_cwd):
    """A half-written file (torn write) is corruption, not absence."""
    _state_path(isolated_cwd).write_text('{"enabled": fal', encoding="utf-8")
    assert kill_switch.is_enabled() is False


def test_empty_file_fails_safe(isolated_cwd):
    _state_path(isolated_cwd).write_text("", encoding="utf-8")
    assert kill_switch.is_enabled() is False


def test_missing_enabled_key_fails_safe(isolated_cwd):
    """Valid JSON that isn't a kill-switch state is as untrustworthy as
    unparseable bytes."""
    _state_path(isolated_cwd).write_text('{"reason": "no enabled key"}', encoding="utf-8")
    assert kill_switch.is_enabled() is False


def test_io_error_fails_safe(isolated_cwd, monkeypatch, caplog):
    """Unreadable ≠ unparseable: the read itself may fail (locked file,
    permissions, disk). chmod is unreliable on Windows, so simulate at the
    Path.read_text layer. The file must EXIST to hit the fail-closed branch.
    """
    _state_path(isolated_cwd).write_text('{"enabled": true}', encoding="utf-8")

    def _boom(self, *args, **kwargs):
        raise OSError("simulated disk read failure")

    monkeypatch.setattr(Path, "read_text", _boom)
    with caplog.at_level(logging.CRITICAL, logger="jarvis.kill_switch"):
        assert kill_switch.is_enabled() is False
    assert any("failing SAFE" in r.message for r in caplog.records)


def test_deleted_file_after_warm_trip_keeps_trip(isolated_cwd):
    """Deletion is NOT corruption, and must not silently re-arm a live trip:
    the warm last-known-good cache answers for a missing file."""
    kill_switch.disable("trip then delete")
    _state_path(isolated_cwd).unlink()
    assert kill_switch.is_enabled() is False
    assert kill_switch.reason() == "trip then delete"


def test_deleted_file_after_warm_armed_stays_armed(isolated_cwd):
    kill_switch.enable()
    _state_path(isolated_cwd).unlink()
    assert kill_switch.is_enabled() is True


def test_corrupt_overrides_warm_armed_cache(isolated_cwd):
    """The fail-closed verdict beats the stale cache in BOTH directions — a
    warm armed cache must not mask a corrupt (possibly externally-tripped)
    file as "still armed"."""
    kill_switch.enable()  # warm the cache in the armed state
    _state_path(isolated_cwd).write_text("###", encoding="utf-8")
    assert kill_switch.is_enabled() is False


def test_corrupt_then_fixed_recovers_immediately(isolated_cwd):
    """The synthetic trip is never cached: the next healthy read wins, in
    either direction."""
    path = _state_path(isolated_cwd)
    path.write_text("{broken", encoding="utf-8")
    assert kill_switch.is_enabled() is False

    path.write_text(
        json.dumps({"enabled": True, "reason": "", "changed_at": "x"}), encoding="utf-8",
    )
    assert kill_switch.is_enabled() is True

    path.write_text(
        json.dumps({"enabled": False, "reason": "real trip", "changed_at": "y"}),
        encoding="utf-8",
    )
    assert kill_switch.is_enabled() is False


def test_enable_repairs_corrupt_file(isolated_cwd):
    """Operator recovery path: /killswitch on rewrites a valid file even when
    the current one is unparseable (enable() must not persist the synthetic
    fail-safe state as-is, nor crash on it)."""
    path = _state_path(isolated_cwd)
    path.write_text("{broken", encoding="utf-8")
    kill_switch.enable()

    assert kill_switch.is_enabled() is True
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["enabled"] is True
    assert on_disk["reason"] == ""


def test_critical_logged_once_per_episode(isolated_cwd, caplog):
    """policy_guard reads on EVERY gated L3 call — a corrupt file must not
    spam CRITICAL per call, but a NEW corruption after recovery must log
    again."""
    path = _state_path(isolated_cwd)
    with caplog.at_level(logging.CRITICAL, logger="jarvis.kill_switch"):
        path.write_text("{broken once", encoding="utf-8")
        for _ in range(3):
            assert kill_switch.is_enabled() is False

        path.write_text(
            json.dumps({"enabled": True, "reason": "", "changed_at": "x"}),
            encoding="utf-8",
        )
        assert kill_switch.is_enabled() is True  # healthy read closes the episode

        path.write_text("{broken twice", encoding="utf-8")
        assert kill_switch.is_enabled() is False
    crits = [r for r in caplog.records if "failing SAFE" in r.message]
    assert len(crits) == 2


def test_audit_event_on_corrupt_state(isolated_cwd):
    _state_path(isolated_cwd).write_text("{broken", encoding="utf-8")
    assert kill_switch.is_enabled() is False

    audit_file = isolated_cwd / "data" / "audit_log.jsonl"
    assert audit_file.exists()
    events = [
        json.loads(line)
        for line in audit_file.read_text(encoding="utf-8").splitlines()
    ]
    matching = [e for e in events if e.get("event") == "kill_switch_state_unreadable"]
    assert len(matching) == 1  # once per episode, mirrors the CRITICAL-log latch
    assert matching[0]["action"] == "fail_safe_trip"


def test_concurrent_read_write_no_crash(isolated_cwd):
    """Readers hammering is_enabled() while a writer rewrites the state file
    must never raise (a torn read at worst yields a transient fail-safe trip)
    and must converge on the last written state."""
    path = _state_path(isolated_cwd)
    errors: list[Exception] = []
    stop = threading.Event()

    def reader():
        while not stop.is_set():
            try:
                kill_switch.is_enabled()
            except Exception as exc:  # pragma: no cover — the assertion target
                errors.append(exc)
                return

    threads = [threading.Thread(target=reader) for _ in range(3)]
    for t in threads:
        t.start()
    try:
        for i in range(30):
            state = {"enabled": i % 2 == 0, "reason": f"w{i}", "changed_at": "c"}
            path.write_text(json.dumps(state), encoding="utf-8")
            time.sleep(0.001)
    finally:
        stop.set()
        for t in threads:
            t.join(timeout=5)

    assert not errors
    path.write_text(
        json.dumps({"enabled": True, "reason": "", "changed_at": "final"}),
        encoding="utf-8",
    )
    assert kill_switch.is_enabled() is True
