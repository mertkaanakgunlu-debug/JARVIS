"""Semantic-memory fact persistence layer for JARVIS (Faz 2).

SQLite store — facts table lives in sessions.db alongside sessions/entities/todos.
Thread-safe, WAL mode. Schema is created idempotently on every _open().

A "fact" is a durable, cross-session statement about the user or their world
(subject/predicate/object triple + a natural-language rendering used for
embedding/recall) — as opposed to episodic memory (raw per-turn conversation
log, session-scoped) or entities (named-thing mentions). Consolidation/dedup
happens at insert time via embedding-similarity lookup in jarvis.memory before
insert_fact() is ever called — see Memory.find_similar_fact().

Usage:
    store = FactStore(Path("data/sessions.db"))
    fid = store.insert_fact("user", "prefers", "dim evening lighting",
                             "User prefers dim lighting in the evenings.", session_id)
    store.bump_fact(fid)  # on a later near-duplicate mention
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


_FACTS_TABLE = """
CREATE TABLE IF NOT EXISTS facts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    subject           TEXT NOT NULL,
    predicate         TEXT NOT NULL,
    object            TEXT NOT NULL,
    fact_text         TEXT NOT NULL,
    session_id_first  TEXT,
    first_seen        TEXT NOT NULL,
    last_seen         TEXT NOT NULL,
    mention_count     INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_facts_last_seen ON facts(last_seen DESC);
"""


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class FactStore:
    """Thread-safe SQLite store for semantic-memory facts."""

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
        conn.executescript(_FACTS_TABLE)
        return conn

    def insert_fact(
        self,
        subject: str,
        predicate: str,
        object_: str,
        fact_text: str,
        session_id: str,
    ) -> int:
        """Insert a new fact row. Returns the new row id."""
        now = _now_iso()
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO facts
                   (subject, predicate, object, fact_text, session_id_first,
                    first_seen, last_seen, mention_count)
                   VALUES (?,?,?,?,?,?,?,1)""",
                (subject, predicate, object_, fact_text, session_id, now, now),
            )
        return cur.lastrowid

    def bump_fact(self, fact_id: int) -> None:
        """Reconfirm an existing fact — bump mention_count and last_seen."""
        with self._lock:
            self._conn.execute(
                "UPDATE facts SET mention_count=mention_count+1, last_seen=? WHERE id=?",
                (_now_iso(), fact_id),
            )

    def get(self, fact_id: int) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
        return dict(row) if row else None

    def list_facts(self, n: int = 50) -> list[dict[str, Any]]:
        """Return the most recently reconfirmed facts first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM facts ORDER BY last_seen DESC LIMIT ?", (n,),
            ).fetchall()
        return [dict(r) for r in rows]

    def total_facts(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM facts").fetchone()
        return row["c"] if row else 0
