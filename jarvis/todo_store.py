"""To-do list persistence layer for JARVIS (Faz 13-D).

SQLite store — todos table lives in sessions.db alongside sessions/entities.
Thread-safe, WAL mode.  Schema is created idempotently on every _open().

Usage:
    store = TodoStore(Path("data/sessions.db"))
    tid = store.add("Sismik analiz raporu yaz", due_date="2026-05-20")
    store.mark_done(tid)
    open_todos = store.list_open()
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from jarvis.clock import local_naive_now


_TODO_TABLE = """
CREATE TABLE IF NOT EXISTS todos (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT,
    priority         TEXT,
    priority_score   REAL DEFAULT 0.5,
    instructions     TEXT,
    due_date         TEXT,
    status           TEXT NOT NULL DEFAULT 'open',
    category         TEXT DEFAULT 'other',
    created_at       TEXT NOT NULL,
    completed_at     TEXT,
    last_reminded_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_todos_status    ON todos(status);
CREATE INDEX IF NOT EXISTS idx_todos_priority  ON todos(priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_todos_due       ON todos(due_date);
"""

PRIORITY_LABELS = {
    "urgent_important": "🔴 Acil & Önemli",
    "important":        "🟡 Önemli",
    "urgent":           "🟠 Acil",
    "low":              "🟢 Düşük",
}

CATEGORY_LABELS = {
    "work":     "İş",
    "personal": "Kişisel",
    "research": "Araştırma",
    "health":   "Sağlık",
    "finance":  "Finans",
    "other":    "Diğer",
}


def _now_iso() -> str:
    # Post-MVP Faz 2: the configured timezone, not the OS's -- see
    # jarvis/clock.py's local_naive_now() for why those are different things.
    return local_naive_now().isoformat(timespec="seconds")


class TodoStore:
    """Thread-safe SQLite store for to-do items."""

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
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_TODO_TABLE)
        return conn

    # ── CRUD ────────────────────────────────────────────────────────────────

    def add(
        self,
        title: str,
        *,
        description: str = "",
        due_date: str = "",
        category: str = "other",
        priority: str = "",
        priority_score: float = 0.5,
        instructions: str = "",
    ) -> str:
        """Insert a new open to-do. Returns ID.  LLM should fill priority + instructions later."""
        tid = str(uuid.uuid4())[:8]
        with self._lock:
            self._conn.execute(
                """INSERT INTO todos
                   (id, title, description, priority, priority_score, instructions,
                    due_date, status, category, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    tid, title, description or None,
                    priority or None, priority_score,
                    instructions or None, due_date or None,
                    "open", category, _now_iso(),
                ),
            )
        return tid

    def update(self, todo_id: str, **fields) -> bool:
        """Update arbitrary fields on a todo. Returns True if found."""
        allowed = {
            "title", "description", "priority", "priority_score",
            "instructions", "due_date", "category",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return False
        cols = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [todo_id]
        with self._lock:
            cur = self._conn.execute(f"UPDATE todos SET {cols} WHERE id=?", vals)
        return cur.rowcount > 0

    def mark_done(self, todo_id: str) -> bool:
        now = _now_iso()
        with self._lock:
            cur = self._conn.execute(
                "UPDATE todos SET status='done', completed_at=? WHERE id=? AND status='open'",
                (now, todo_id),
            )
        return cur.rowcount > 0

    def delete(self, todo_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM todos WHERE id=?", (todo_id,))
        return cur.rowcount > 0

    def get(self, todo_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
        return dict(row) if row else None

    # ── Queries ─────────────────────────────────────────────────────────────

    def list_open(self, category: str = "") -> list[dict[str, Any]]:
        """Return open todos ordered by priority_score DESC, due_date ASC."""
        if category:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM todos WHERE status='open' AND category=? "
                    "ORDER BY priority_score DESC, due_date ASC NULLS LAST",
                    (category,),
                ).fetchall()
        else:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT * FROM todos WHERE status='open' "
                    "ORDER BY priority_score DESC, due_date ASC NULLS LAST"
                ).fetchall()
        return [dict(r) for r in rows]

    def list_done(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM todos WHERE status='done' ORDER BY completed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def top_open(self, n: int = 5) -> list[dict[str, Any]]:
        """Top N open todos by priority score — for system prompt injection."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM todos WHERE status='open' "
                "ORDER BY priority_score DESC, due_date ASC NULLS LAST LIMIT ?",
                (n,),
            ).fetchall()
        return [dict(r) for r in rows]

    def due_soon(self, within_min: int = 120) -> list[dict[str, Any]]:
        """Return open todos with due_date within the next N minutes."""
        from datetime import timedelta
        cutoff = (local_naive_now() + timedelta(minutes=within_min)).isoformat(timespec="seconds")
        now = _now_iso()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM todos WHERE status='open' AND due_date IS NOT NULL "
                "AND due_date <= ? AND due_date >= ? ORDER BY due_date",
                (cutoff, now),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_reminded(self, todo_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE todos SET last_reminded_at=? WHERE id=?", (_now_iso(), todo_id)
            )

    def count_open(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM todos WHERE status='open'"
            ).fetchone()[0]

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
