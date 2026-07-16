"""JARVIS_HOME isolation root (stabilization sprint — GPT plan F.8).

Locks in the contract of jarvis/paths.py: with JARVIS_HOME set, every store
resolves under it regardless of cwd — including the project-root-anchored
OAuth token paths that a chdir-based isolation would miss. With it unset,
behavior is byte-identical to the historical cwd-relative layout.
"""
from __future__ import annotations

import os
from pathlib import Path

from jarvis import paths


def test_home_unset_falls_back_to_cwd(monkeypatch):
    monkeypatch.delenv("JARVIS_HOME", raising=False)
    assert paths.jarvis_home() == Path(".")
    assert paths.data_dir() == Path("data")
    assert paths.resolve(Path("vault")) == Path(".") / "vault"


def test_home_set_redirects_all_roots(jarvis_home):
    assert paths.jarvis_home() == jarvis_home
    assert paths.data_dir() == jarvis_home / "data"
    assert paths.resolve(Path("vault")) == jarvis_home / "vault"
    # Project-root-anchored family follows JARVIS_HOME too — the isolation
    # hole a cwd-only scheme leaves open (real gmail/calendar tokens).
    assert paths.project_data_dir() == jarvis_home / "data"
    assert paths.resolve_project("data/calendar_credentials.json") == (
        jarvis_home / "data" / "calendar_credentials.json"
    )


def test_home_unset_project_paths_anchor_to_repo_root(monkeypatch):
    monkeypatch.delenv("JARVIS_HOME", raising=False)
    repo_root = Path(__file__).resolve().parent.parent
    assert paths.project_data_dir() == repo_root / "data"
    assert paths.resolve_project("data/x.json") == repo_root / "data" / "x.json"


def test_absolute_paths_pass_through(jarvis_home):
    abs_p = Path("C:/somewhere/else/creds.json")
    assert paths.resolve(abs_p) == abs_p
    assert paths.resolve_project(abs_p) == abs_p


def test_audit_log_writes_under_home_not_cwd(jarvis_home, isolated_cwd):
    """audit_log must follow JARVIS_HOME even when cwd points elsewhere."""
    from jarvis import audit_log

    audit_log.record("decision", tool="x", ruling="auto_approved")
    target = jarvis_home / "data" / "audit_log.jsonl"
    assert target.exists(), "audit entry must land under JARVIS_HOME"
    # And nothing under the (different) cwd:
    assert not (isolated_cwd / "data" / "audit_log.jsonl").exists()
    entries = audit_log.tail(5)
    assert entries and entries[-1]["tool"] == "x"


def test_kill_switch_writes_under_home_not_cwd(jarvis_home, isolated_cwd):
    from jarvis import kill_switch

    kill_switch.disable("test trip")
    try:
        target = jarvis_home / "data" / "kill_switch.json"
        assert target.exists()
        assert not (isolated_cwd / "data" / "kill_switch.json").exists()
        assert kill_switch.is_enabled() is False
    finally:
        kill_switch.enable()


def test_env_read_per_call_no_stale_cache(tmp_path, monkeypatch):
    """paths must re-read JARVIS_HOME on every call — a fixture changing the
    env mid-process takes effect immediately (the kill_switch._cache lesson)."""
    a, b = tmp_path / "a", tmp_path / "b"
    monkeypatch.setenv("JARVIS_HOME", str(a))
    assert paths.data_dir() == a / "data"
    monkeypatch.setenv("JARVIS_HOME", str(b))
    assert paths.data_dir() == b / "data"
    monkeypatch.delenv("JARVIS_HOME")
    assert paths.data_dir() == Path("data")


def test_real_project_data_untouched(jarvis_home, isolated_cwd):
    """The whole point: exercising stores under the fixtures leaves the real
    repo's data/ exactly as it was."""
    repo_root = Path(__file__).resolve().parent.parent
    real_data = repo_root / "data"
    before = {
        p: p.stat().st_mtime_ns
        for p in (real_data.rglob("*") if real_data.exists() else [])
        if p.is_file()
    }

    from jarvis import audit_log, kill_switch
    from jarvis.session_store import SessionStore

    audit_log.record("execution_start", tool="y")
    kill_switch.status()
    store = SessionStore(paths.data_dir() / "sessions.db")
    sid = store.new_session()
    assert sid
    assert (jarvis_home / "data" / "sessions.db").exists()

    after = {
        p: p.stat().st_mtime_ns
        for p in (real_data.rglob("*") if real_data.exists() else [])
        if p.is_file()
    }
    assert before == after, "real data/ must not change under JARVIS_HOME isolation"


def test_workspace_derived_outputs_follow_home(jarvis_home, isolated_cwd):
    """pdf cache default (tools/pdf.py fallback) resolves under JARVIS_HOME."""
    from jarvis.tools import drive, geo_math_tool

    assert drive._token_file() == jarvis_home / "data" / ".drive_token.json"
    assert drive._cache_dir() == jarvis_home / "data" / "drive_cache"
    assert geo_math_tool._output_dir() == jarvis_home / "data" / "geo_math_outputs"


def test_gmail_calendar_token_paths_follow_home(jarvis_home):
    from jarvis.tools import gmail, calendar

    assert gmail._token_file() == jarvis_home / "data" / ".gmail_token.json"
    assert calendar._token_file() == jarvis_home / "data" / ".calendar_token.json"
    assert "JARVIS_HOME" in os.environ  # sanity: fixture actually set it
