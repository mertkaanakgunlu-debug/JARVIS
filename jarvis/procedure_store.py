"""Procedural-memory persistence layer for JARVIS (Faz 2).

SQLite store — procedures table lives in sessions.db alongside sessions/todos/facts.
Thread-safe, WAL mode. Schema is created idempotently on every _open().

A "procedure" is a named, reusable instructional workflow (free-form markdown
body, same style as the pre-Faz-2 prompts/workflows/*.md files) that gets
semantically matched against the user's query each turn (see
jarvis.memory.Memory.recall_procedures) instead of the old hardcoded
keyword-substring trigger. `source='seed'` rows are migrated from the
static workflow files at startup; `source='agent'` rows are added at runtime
via the procedure_save tool when the agent judges a completed task worth
remembering.

Usage:
    store = ProcedureStore(Path("data/sessions.db"))
    pid = store.add("data_report", "Data analysis + PDF report workflow", body_text)
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


_PROCEDURE_TABLE = """
CREATE TABLE IF NOT EXISTS procedures (
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

# GPT-5.6 review remediation (2026-07-15), Faz 3 — procedural-memory
# poisoning defense: status/created_by/approved_at didn't exist before this.
# CREATE TABLE IF NOT EXISTS above only covers a brand-new db file; an
# existing sessions.db needs these columns added explicitly. status's
# DEFAULT 'approved' grandfathers every pre-Faz-3 row (behavior no-op for
# anything saved before this) — only add()'s explicit status= argument
# (default_status_for_source) governs NEW rows going forward.
_MIGRATION_COLUMNS = {
    "status": "ALTER TABLE procedures ADD COLUMN status TEXT NOT NULL DEFAULT 'approved'",
    "created_by": "ALTER TABLE procedures ADD COLUMN created_by TEXT",
    "approved_at": "ALTER TABLE procedures ADD COLUMN approved_at TEXT",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def default_status_for_source(source: str) -> str:
    """'seed' (static workflow files migrated at startup, e.g. data_report.md)
    is grandfathered trusted -> 'approved' immediately. Everything else
    (today, only 'agent' — the procedure_save tool) starts as 'draft' and is
    excluded from jarvis.memory.Memory.recall_procedures() until a human
    approves it via /procedures — closes the gap where an agent could write
    a procedure that got silently recalled and re-fed into its own future
    system prompt with zero review (GPT-5.6 review, procedural-memory
    poisoning)."""
    return "approved" if source == "seed" else "draft"


class ProcedureStore:
    """Thread-safe SQLite store for procedural-memory workflows."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = self._open(db_path)

    @staticmethod
    def _open(db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_PROCEDURE_TABLE)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(procedures)").fetchall()}
        for column, ddl in _MIGRATION_COLUMNS.items():
            if column not in existing:
                conn.execute(ddl)
        return conn

    def add(self, name: str, description: str, body: str, source: str = "agent", created_by: str = "") -> int:
        """Insert a new procedure. Returns the new row id. status is derived
        from source, not caller-settable — see default_status_for_source()."""
        status = default_status_for_source(source)
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO procedures (name, description, body, source, status, created_by, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (name, description, body, source, status, created_by, _now_iso()),
            )
        return cur.lastrowid

    def get_all(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM procedures").fetchall()
        return [dict(r) for r in rows]

    def get(self, procedure_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM procedures WHERE id=?", (procedure_id,)).fetchone()
        return dict(row) if row else None

    def get_drafts(self) -> list[dict[str, Any]]:
        """Procedures awaiting human approval — see /procedures in cli.py."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM procedures WHERE status='draft' ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def approve(self, procedure_id: int) -> bool:
        """Mark a draft procedure approved. Caller must also update the
        Chroma-side copy (jarvis.memory.Memory.approve_procedure) — the two
        stores are kept in sync by the caller, not automatically. Returns
        False if no such draft id exists."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE procedures SET status='approved', approved_at=? WHERE id=? AND status='draft'",
                (_now_iso(), procedure_id),
            )
        return cur.rowcount > 0

    def reject(self, procedure_id: int) -> bool:
        """Delete a draft procedure outright — reject means discard, not just
        hide; a rejected agent-written procedure has no legitimate future
        use. Only ever touches status='draft' rows, never an already-
        approved one. Caller must also clean up the Chroma-side copy
        (jarvis.memory.Memory.delete_procedure)."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM procedures WHERE id=? AND status='draft'", (procedure_id,))
        return cur.rowcount > 0

    def mark_used(self, procedure_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE procedures SET use_count=use_count+1, last_used_at=? WHERE id=?",
                (_now_iso(), procedure_id),
            )

    def total(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM procedures").fetchone()
        return row["c"] if row else 0
