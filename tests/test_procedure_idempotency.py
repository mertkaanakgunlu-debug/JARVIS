"""Patch 1.2 Faz 1C — ProcedureStore content idempotency + safe migration.

Live anchor F16 (2026-07-16): one looping turn produced ~10 identical
procedure_save calls and the store accepted every INSERT (ids 2..11, all
drafts named brew_coffee). Two layers fixed here:
  * add_or_get() — same normalized content returns the existing row,
  * open-time migration — a db that ALREADY holds such duplicates gets them
    archived (never deleted) before the UNIQUE index is created, else the
    index build itself would fail on exactly the damage it's meant to prevent.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from jarvis.procedure_store import (
    ProcedureAddResult,
    ProcedureStore,
    compute_fingerprint,
)


def _store(tmp_path: Path) -> ProcedureStore:
    return ProcedureStore(tmp_path / "sessions.db")


# ── add_or_get ────────────────────────────────────────────────────────────────

def test_identical_resave_returns_existing_row(tmp_path):
    store = _store(tmp_path)
    first = store.add_or_get("brew_coffee", "Morning coffee", "1. Fill water\n2. Start")
    second = store.add_or_get("brew_coffee", "Morning coffee", "1. Fill water\n2. Start")

    assert isinstance(first, ProcedureAddResult)
    assert first.created is True
    assert second.created is False
    assert second.procedure_id == first.procedure_id
    live = [p for p in store.get_all() if p["status"] != "archived_duplicate"]
    assert len(live) == 1


def test_whitespace_and_case_variants_dedupe(tmp_path):
    store = _store(tmp_path)
    a = store.add_or_get("brew_coffee", "Morning  coffee", "1. Fill water")
    b = store.add_or_get("Brew_Coffee", "morning coffee", "1.  Fill   water")
    assert b.created is False
    assert b.procedure_id == a.procedure_id


def test_different_content_creates_new_row(tmp_path):
    store = _store(tmp_path)
    a = store.add_or_get("brew_coffee", "Morning coffee", "1. Fill water")
    b = store.add_or_get("brew_coffee", "Morning coffee", "1. Grind beans first")
    assert b.created is True
    assert b.procedure_id != a.procedure_id


def test_add_shim_dedupes_too(tmp_path):
    store = _store(tmp_path)
    pid1 = store.add("x", "desc", "body")
    pid2 = store.add("x", "desc", "body")
    assert pid1 == pid2


def test_reject_frees_the_fingerprint(tmp_path):
    store = _store(tmp_path)
    first = store.add_or_get("x", "desc", "body")  # agent source → draft
    assert store.reject(first.procedure_id) is True
    again = store.add_or_get("x", "desc", "body")
    assert again.created is True


# ── open-time migration over a legacy, duplicate-bearing db (F16 damage) ─────

_LEGACY_TABLE = """
CREATE TABLE procedures (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    description   TEXT NOT NULL,
    body          TEXT NOT NULL,
    source        TEXT DEFAULT 'agent',
    status        TEXT NOT NULL DEFAULT 'approved',
    created_by    TEXT,
    approved_at   TEXT,
    created_at    TEXT NOT NULL,
    last_used_at  TEXT,
    use_count     INTEGER DEFAULT 0
);
"""


def _legacy_db_with_duplicates(tmp_path: Path) -> Path:
    db = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_LEGACY_TABLE)
    rows = [
        # three identical drafts (F16 shape) + one APPROVED twin of the same content
        ("brew_coffee", "Morning coffee", "1. Fill water", "agent", "draft"),
        ("brew_coffee", "Morning coffee", "1. Fill water", "agent", "draft"),
        ("brew_coffee", "Morning coffee", "1. Fill water", "agent", "approved"),
        ("brew_coffee", "Morning coffee", "1. Fill water", "agent", "draft"),
        # one genuinely distinct procedure — must be untouched
        ("data_report", "Data analysis workflow", "1. Load CSV", "seed", "approved"),
    ]
    for name, desc, body, source, status in rows:
        conn.execute(
            "INSERT INTO procedures (name, description, body, source, status, created_at) "
            "VALUES (?,?,?,?,?,'2026-07-16T15:00:00')",
            (name, desc, body, source, status),
        )
    conn.commit()
    conn.close()
    return db


def test_migration_archives_duplicates_keeps_approved_canonical(tmp_path):
    db = _legacy_db_with_duplicates(tmp_path)
    store = ProcedureStore(db)

    # 4 same-content rows → 1 canonical + 3 archived; the approved twin wins
    assert len(store.archived_duplicate_ids) == 3
    live = [p for p in store.get_all() if p["status"] != "archived_duplicate"]
    brew = [p for p in live if p["name"] == "brew_coffee"]
    assert len(brew) == 1
    assert brew[0]["status"] == "approved"  # canonical preference
    assert brew[0]["id"] == 3               # the approved row, not the earliest draft
    # distinct procedure untouched
    assert any(p["name"] == "data_report" for p in live)
    # drafts list no longer shows the archived copies
    assert store.get_drafts() == []


def test_migration_backfills_fingerprints_and_indexes(tmp_path):
    db = _legacy_db_with_duplicates(tmp_path)
    store = ProcedureStore(db)

    for p in store.get_all():
        assert p["fingerprint"], f"row {p['id']} missing fingerprint"
    # unique index actually enforces: direct duplicate INSERT must fail
    fp = compute_fingerprint("brew_coffee", "Morning coffee", "1. Fill water", "agent")
    conn = sqlite3.connect(str(db))
    try:
        import pytest
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO procedures (name, description, body, source, status, created_at, fingerprint) "
                "VALUES ('brew_coffee','Morning coffee','1. Fill water','agent','draft','now',?)",
                (fp,),
            )
    finally:
        conn.close()


def test_reopen_is_idempotent(tmp_path):
    db = _legacy_db_with_duplicates(tmp_path)
    first = ProcedureStore(db)
    assert len(first.archived_duplicate_ids) == 3
    second = ProcedureStore(db)  # re-open: nothing new to archive
    assert second.archived_duplicate_ids == []
