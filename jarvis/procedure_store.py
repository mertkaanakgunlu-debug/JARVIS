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

import hashlib
import sqlite3
import threading
from dataclasses import dataclass
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
    use_count     INTEGER DEFAULT 0,
    fingerprint   TEXT
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
    # Patch 1.2 (Faz 1C): content identity for idempotency — see add_or_get().
    "fingerprint": "ALTER TABLE procedures ADD COLUMN fingerprint TEXT",
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize(text: str) -> str:
    """Whitespace-collapsed, case-folded — cosmetic edits don't defeat identity."""
    return " ".join((text or "").split()).lower()


def compute_fingerprint(name: str, description: str, body: str, source: str = "agent") -> str:
    """Content identity of a procedure: same (normalized) fields ⇒ same hash."""
    payload = "\x1f".join((_normalize(name), _normalize(description), _normalize(body), source))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProcedureAddResult:
    """Store-layer answer to add_or_get() — the caller renders any user/model
    text (layer separation: the store never returns display strings)."""
    procedure_id: int
    created: bool


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
        self._conn, self.archived_duplicate_ids = self._open(db_path)

    @staticmethod
    def _open(db_path: Path) -> tuple[sqlite3.Connection, list[int]]:
        conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_PROCEDURE_TABLE)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(procedures)").fetchall()}
        for column, ddl in _MIGRATION_COLUMNS.items():
            if column not in existing:
                conn.execute(ddl)

        # ── Patch 1.2 (Faz 1C) idempotency migration, in strict order ──
        # A pre-existing db can already hold content-duplicate rows (live
        # incident F16: procedure_save re-issued ~10 times, every INSERT
        # accepted), so the UNIQUE index CANNOT be created first: backfill
        # fingerprints, archive the duplicate rows, THEN index.
        # (1) backfill fingerprints for legacy rows
        for r in conn.execute(
            "SELECT id, name, description, body, source FROM procedures WHERE fingerprint IS NULL"
        ).fetchall():
            conn.execute(
                "UPDATE procedures SET fingerprint=? WHERE id=?",
                (compute_fingerprint(r["name"], r["description"], r["body"], r["source"] or "agent"), r["id"]),
            )
        # (2) archive non-canonical duplicates. Canonical = an approved row if
        # any (never demote an approved copy in favor of a draft twin), else
        # the earliest row. Archived rows keep their content but leave every
        # live query (drafts list, recall sync, the unique index below).
        archived: list[int] = []
        for g in conn.execute(
            "SELECT fingerprint FROM procedures WHERE status != 'archived_duplicate' "
            "GROUP BY fingerprint HAVING COUNT(*) > 1"
        ).fetchall():
            rows = conn.execute(
                "SELECT id FROM procedures WHERE fingerprint=? AND status != 'archived_duplicate' "
                "ORDER BY (status='approved') DESC, id ASC",
                (g["fingerprint"],),
            ).fetchall()
            for r in rows[1:]:
                conn.execute("UPDATE procedures SET status='archived_duplicate' WHERE id=?", (r["id"],))
                archived.append(r["id"])
        # (3) unique index over live rows only — archived history stays put.
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_procedures_fingerprint_live "
            "ON procedures(fingerprint) WHERE status != 'archived_duplicate'"
        )
        return conn, archived

    def add_or_get(
        self, name: str, description: str, body: str,
        source: str = "agent", created_by: str = "",
    ) -> ProcedureAddResult:
        """Idempotent insert: same (normalized) content ⇒ the existing row.

        The check-then-insert runs under the store lock (single writer per
        process); the partial UNIQUE index backstops cross-process races.
        status is derived from source, not caller-settable — see
        default_status_for_source().
        """
        status = default_status_for_source(source)
        fp = compute_fingerprint(name, description, body, source)
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM procedures WHERE fingerprint=? AND status != 'archived_duplicate'",
                (fp,),
            ).fetchone()
            if row:
                return ProcedureAddResult(procedure_id=row["id"], created=False)
            cur = self._conn.execute(
                """INSERT INTO procedures (name, description, body, source, status, created_by, created_at, fingerprint)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (name, description, body, source, status, created_by, _now_iso(), fp),
            )
        return ProcedureAddResult(procedure_id=cur.lastrowid, created=True)

    def add(self, name: str, description: str, body: str, source: str = "agent", created_by: str = "") -> int:
        """Back-compat shim over add_or_get() — returns the row id either way.
        (Pre-Patch-1.2 this was an unconditional INSERT; the F16 duplicate
        pile-up came through exactly here.)"""
        return self.add_or_get(name, description, body, source, created_by).procedure_id

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
