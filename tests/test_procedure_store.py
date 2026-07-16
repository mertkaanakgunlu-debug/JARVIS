"""jarvis/procedure_store.py + jarvis/memory.py's procedure recall -- Faz 3
of the GPT-5.6 review remediation plan (procedural-memory poisoning defense).

Before this fix, procedure_store had no provenance/approval concept beyond
`source` -- an agent-written procedure (procedure_save) was immediately
recallable and re-injected into a future turn's system prompt with zero
human review. Now: source='agent' rows default to status='draft' and are
excluded from jarvis.memory.Memory.recall_procedures() until approved.
"""
from __future__ import annotations

import sqlite3

from jarvis.config import Settings
from jarvis.memory import Memory
from jarvis.procedure_store import ProcedureStore, default_status_for_source


# ── SQLite layer ─────────────────────────────────────────────────────────────

def test_agent_sourced_procedure_defaults_to_draft(tmp_path):
    store = ProcedureStore(tmp_path / "sessions.db")
    pid = store.add("test_proc", "does a thing", "1. do it", source="agent")
    row = store.get(pid)
    assert row["status"] == "draft"
    assert row["approved_at"] is None


def test_seed_procedure_defaults_to_approved(tmp_path):
    store = ProcedureStore(tmp_path / "sessions.db")
    pid = store.add("seeded_proc", "does a thing", "1. do it", source="seed")
    row = store.get(pid)
    assert row["status"] == "approved"


def test_default_status_for_source():
    assert default_status_for_source("agent") == "draft"
    assert default_status_for_source("seed") == "approved"


def test_approve_flips_draft_to_approved(tmp_path):
    store = ProcedureStore(tmp_path / "sessions.db")
    pid = store.add("test_proc", "d", "b", source="agent")

    assert store.approve(pid) is True
    row = store.get(pid)
    assert row["status"] == "approved"
    assert row["approved_at"]


def test_approve_is_a_noop_for_unknown_id(tmp_path):
    store = ProcedureStore(tmp_path / "sessions.db")
    assert store.approve(999) is False


def test_reject_deletes_draft(tmp_path):
    store = ProcedureStore(tmp_path / "sessions.db")
    pid = store.add("test_proc", "d", "b", source="agent")

    assert store.reject(pid) is True
    assert store.get(pid) is None


def test_reject_does_not_touch_an_already_approved_row(tmp_path):
    store = ProcedureStore(tmp_path / "sessions.db")
    pid = store.add("seeded_proc", "d", "b", source="seed")  # status=approved

    assert store.reject(pid) is False
    assert store.get(pid) is not None


def test_get_drafts_only_returns_draft_rows(tmp_path):
    store = ProcedureStore(tmp_path / "sessions.db")
    draft_id = store.add("draft_proc", "d", "b", source="agent")
    store.add("seeded_proc", "d", "b", source="seed")

    drafts = store.get_drafts()
    assert [r["id"] for r in drafts] == [draft_id]


def test_migration_adds_columns_and_grandfathers_existing_rows_as_approved(tmp_path):
    """Simulates a pre-Faz-3 sessions.db: create the table with the OLD schema
    (no status/created_by/approved_at) and insert a row directly, then open it
    with the current ProcedureStore and confirm the migration ran and the
    pre-existing row was grandfathered to 'approved' (not silently hidden
    from recall by a migration that defaulted everything to 'draft')."""
    db_path = tmp_path / "sessions.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE procedures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            body TEXT NOT NULL,
            source TEXT DEFAULT 'agent',
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            use_count INTEGER DEFAULT 0
        );
    """)
    conn.execute(
        "INSERT INTO procedures (name, description, body, source, created_at) VALUES (?,?,?,?,?)",
        ("old_proc", "pre-existing", "1. legacy", "agent", "2026-01-01T00:00:00"),
    )
    conn.commit()
    conn.close()

    store = ProcedureStore(db_path)
    rows = store.get_all()
    assert len(rows) == 1
    assert rows[0]["status"] == "approved"
    assert rows[0]["name"] == "old_proc"


# ── Memory (Chroma) recall layer ─────────────────────────────────────────────

def test_draft_procedure_not_recalled_until_approved(isolated_cwd):
    settings = Settings()
    memory = Memory(settings)
    store = ProcedureStore(isolated_cwd / "data" / "sessions.db")

    pid = store.add(
        "budget_chart_report", "generate a monthly budget chart and PDF report",
        "1. sync finance  2. chart  3. write report", source="agent",
    )
    status = default_status_for_source("agent")
    memory.store_procedure(pid, "budget_chart_report", "generate a monthly budget chart and PDF report",
                            "1. sync finance  2. chart  3. write report", status=status)

    hits = memory.recall_procedures("generate a monthly budget chart and PDF report", n=1, distance_max=1.5)
    assert hits == []

    store.approve(pid)
    memory.approve_procedure(pid, "budget_chart_report", "1. sync finance  2. chart  3. write report")

    hits = memory.recall_procedures("generate a monthly budget chart and PDF report", n=1, distance_max=1.5)
    assert len(hits) == 1
    assert hits[0]["name"] == "budget_chart_report"


def test_rejected_procedure_is_removed_from_chroma(isolated_cwd):
    settings = Settings()
    memory = Memory(settings)
    store = ProcedureStore(isolated_cwd / "data" / "sessions.db")

    pid = store.add("throwaway_proc", "a workflow nobody wants", "1. oops", source="agent")
    memory.store_procedure(pid, "throwaway_proc", "a workflow nobody wants", "1. oops", status="draft")
    assert memory.count_procedures() == 1

    store.reject(pid)
    memory.delete_procedure(pid)
    assert memory.count_procedures() == 0
