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
    created_at    TEXT NOT NULL,
    last_used_at  TEXT,
    use_count     INTEGER DEFAULT 0
);
"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


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
        return conn

    def add(self, name: str, description: str, body: str, source: str = "agent") -> int:
        """Insert a new procedure. Returns the new row id."""
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO procedures (name, description, body, source, created_at)
                   VALUES (?,?,?,?,?)""",
                (name, description, body, source, _now_iso()),
            )
        return cur.lastrowid

    def get_all(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM procedures").fetchall()
        return [dict(r) for r in rows]

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
