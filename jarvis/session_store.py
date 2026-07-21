"""Persistent session store for JARVIS (Faz 12-B).

SQLite database at data/sessions.db with three tables:
  sessions  — one row per conversation session
  messages  — one turn_idx bucket per session, each holding a FULL cumulative
              snapshot of that session's (trimmed) history — not a delta.
              save_turn() drops all older buckets for the session, so exactly
              one survives at a time; readers only ever need MAX(turn_idx).
  entities  — named entities extracted across all turns

Design decisions:
- WAL journal mode + autocommit for FastAPI concurrent access safety
- threading.Lock guards ALL reads and writes (single-process, multi-thread via
  FastAPI/TaskExecutor) — a read must never observe a write mid-flight
- Multi-statement writes (save_turn) run inside an explicit BEGIN/COMMIT so a
  crash or exception can't leave a session's messages half-deleted
- langchain_core.load.dumps/loads preserves tool_calls in AIMessage/ToolMessage
- Corrupt DB → rename to .corrupt-<ts> + rebuild (never silently lose data)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import date, datetime
from pathlib import Path

from langchain_core.load import dumps as lc_dumps, loads as lc_loads
from langchain_core.messages import BaseMessage


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    created_at   TEXT NOT NULL,
    last_active  TEXT NOT NULL,
    message_count INTEGER DEFAULT 0,
    topic_hint   TEXT,
    status       TEXT DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS messages (
    session_id   TEXT NOT NULL,
    turn_idx     INTEGER NOT NULL,
    msg_idx      INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    ts           TEXT NOT NULL,
    PRIMARY KEY (session_id, turn_idx, msg_idx),
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages(session_id, turn_idx, msg_idx);

CREATE TABLE IF NOT EXISTS entities (
    name             TEXT NOT NULL,
    type             TEXT NOT NULL,
    description      TEXT,
    first_seen       TEXT NOT NULL,
    last_seen        TEXT NOT NULL,
    mention_count    INTEGER DEFAULT 1,
    session_id_first TEXT,
    PRIMARY KEY (name, type)
);
"""


class SessionStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = self._open(db_path)

    # ── Connection management ─────────────────────────────────────────────────

    @staticmethod
    def _apply_migrations(conn: sqlite3.Connection) -> None:
        """Idempotent schema migrations — run after executescript(_SCHEMA)."""
        # Faz 13-A: summary columns
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "summary" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN summary TEXT")
        if "summary_embedded_at" not in cols:
            conn.execute("ALTER TABLE sessions ADD COLUMN summary_embedded_at TEXT")
        # Faz 13-C: scheduled_tasks table (scheduler.py owns schema, we just ensure it's created)
        from jarvis.scheduler import _SCHEDULE_TABLE
        conn.executescript(_SCHEDULE_TABLE)
        # Faz 13-D: todos table
        from jarvis.todo_store import _TODO_TABLE
        conn.executescript(_TODO_TABLE)
        # Faz 16: finance tables
        from jarvis.finance_store import _FINANCE_TABLES
        conn.executescript(_FINANCE_TABLES)
        # Faz 2: semantic memory (facts) + procedural memory (procedures) tables
        from jarvis.facts_store import _FACTS_TABLE
        conn.executescript(_FACTS_TABLE)
        from jarvis.procedure_store import _PROCEDURE_TABLE
        conn.executescript(_PROCEDURE_TABLE)

    def _open(self, path: Path) -> sqlite3.Connection:
        conn = None
        try:
            conn = sqlite3.connect(
                str(path),
                check_same_thread=False,
                isolation_level=None,   # autocommit — WAL handles concurrency
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            self._apply_migrations(conn)
            return conn
        except sqlite3.DatabaseError:
            # Close before rename — required on Windows (file lock)
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            corrupt = path.with_suffix(f".db.corrupt-{ts}")
            try:
                path.rename(corrupt)
                print(f"[JARVIS] sessions.db was corrupt — renamed to {corrupt.name}, starting fresh.")
            except OSError:
                # If rename still fails (e.g. WAL sidecar locked), just delete
                try:
                    path.unlink(missing_ok=True)
                    print("[JARVIS] sessions.db was corrupt — deleted, starting fresh.")
                except OSError:
                    pass
            conn = sqlite3.connect(
                str(path),
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            self._apply_migrations(conn)
            return conn

    # ── Session management ────────────────────────────────────────────────────

    def new_session(self, topic_hint: str | None = None) -> str:
        sid = date.today().strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:4]
        now = datetime.now().isoformat()
        with self._lock:
            self._conn.execute(
                "INSERT INTO sessions(id, created_at, last_active, topic_hint, status) "
                "VALUES (?, ?, ?, ?, 'active')",
                (sid, now, now, topic_hint),
            )
        return sid

    def latest_session(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM sessions WHERE status='active' "
                "ORDER BY last_active DESC LIMIT 1"
            ).fetchone()
        return row["id"] if row else None

    def session_exists(self, session_id: str) -> bool:
        """Agent Runtime rev.2, Faz 5: lets a caller validate an explicit
        resume id (e.g. JarvisAgent's resume_session_id) before trusting it
        -- a stale/foreign id (deleted session, different JARVIS_HOME) must
        fall back to a fresh session, not silently attach history writes to
        a session_id with no row in this table. status is deliberately not
        filtered here (unlike latest_session()) -- an explicit request to
        resume a specific id should work even if it was archived; only the
        old *guessing* behavior needed to stay within "active" sessions."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM sessions WHERE id=? LIMIT 1", (session_id,)
            ).fetchone()
        return row is not None

    def archive_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET status='archived' WHERE id=?",
                (session_id,),
            )

    def set_topic_hint(self, session_id: str, hint: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET topic_hint=? WHERE id=?",
                (hint[:60], session_id),
            )

    def list_sessions(self, n: int = 10) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created_at, last_active, message_count, topic_hint, status "
                "FROM sessions ORDER BY last_active DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [dict(r) for r in rows]

    def total_sessions(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()
        return row["c"] if row else 0

    # ── Message persistence ───────────────────────────────────────────────────

    def save_turn(
        self,
        session_id: str,
        messages: list[BaseMessage],
        turn_idx: int,
    ) -> None:
        """Persist the full (trimmed) history snapshot as of this turn.

        Drops every turn_idx bucket <= this one for the session first — each
        snapshot is self-contained (not a delta), so keeping old buckets
        around only wastes space and, if a turn_idx is ever reused (e.g.
        after switch_session), would resurrect stale rows on read.
        """
        ts = datetime.now().isoformat()
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                self._conn.execute(
                    "DELETE FROM messages WHERE session_id=? AND turn_idx<=?",
                    (session_id, turn_idx),
                )
                for i, msg in enumerate(messages):
                    try:
                        payload = lc_dumps(msg)
                    except Exception:
                        # Fallback: bare dict for types langchain_core can't serialize
                        payload = json.dumps({"type": type(msg).__name__, "content": str(msg.content)})
                    self._conn.execute(
                        "INSERT INTO messages(session_id, turn_idx, msg_idx, payload_json, ts) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (session_id, turn_idx, i, payload, ts),
                    )
                # Update session metadata
                self._conn.execute(
                    "UPDATE sessions SET last_active=?, message_count=? WHERE id=?",
                    (ts, len(messages), session_id),
                )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise

    def load_history(self, session_id: str, limit: int = 20) -> list[BaseMessage]:
        """Return the latest saved snapshot of non-system messages for a session.

        Each turn_idx bucket is a full cumulative snapshot, not a delta —
        reading only the single latest bucket (MAX(turn_idx)) avoids
        re-introducing earlier snapshots as duplicates. Older buckets may
        still exist on disk from before save_turn started collapsing them.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM messages "
                "WHERE session_id=? AND turn_idx=("
                "  SELECT MAX(turn_idx) FROM messages WHERE session_id=?"
                ") ORDER BY msg_idx",
                (session_id, session_id),
            ).fetchall()

        messages: list[BaseMessage] = []
        for row in rows:
            try:
                messages.append(lc_loads(row["payload_json"]))
            except Exception:
                pass

        from langchain_core.messages import SystemMessage
        non_system = [m for m in messages if not isinstance(m, SystemMessage)]
        return non_system[-limit:]

    def last_turn_idx(self, session_id: str) -> int:
        """Highest turn_idx saved for this session (0 if none yet).

        Used to resume the turn counter on session re-entry — restarting it
        at 0 would let a new turn reuse an old LangGraph thread_id
        (`{session_id}-t{turn}`) and resurrect a stale checkpoint.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(turn_idx), 0) AS m FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
        return row["m"] if row else 0

    # ── Entity memory ─────────────────────────────────────────────────────────

    def upsert_entity(
        self,
        name: str,
        entity_type: str,
        description: str,
        session_id: str,
    ) -> None:
        now = datetime.now().isoformat()
        with self._lock:
            existing = self._conn.execute(
                "SELECT mention_count FROM entities WHERE name=? AND type=?",
                (name, entity_type),
            ).fetchone()
            if existing:
                self._conn.execute(
                    "UPDATE entities SET description=?, last_seen=?, mention_count=mention_count+1 "
                    "WHERE name=? AND type=?",
                    (description, now, name, entity_type),
                )
            else:
                self._conn.execute(
                    "INSERT INTO entities(name, type, description, first_seen, last_seen, "
                    "mention_count, session_id_first) VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (name, entity_type, description, now, now, session_id),
                )

    def top_entities(self, n: int = 20) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, type, description, mention_count, last_seen "
                "FROM entities ORDER BY mention_count DESC, last_seen DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [dict(r) for r in rows]

    def search_entities(self, query: str) -> list[dict]:
        pattern = f"%{query}%"
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, type, description, mention_count, last_seen "
                "FROM entities WHERE name LIKE ? OR description LIKE ? "
                "ORDER BY mention_count DESC LIMIT 10",
                (pattern, pattern),
            ).fetchall()
        return [dict(r) for r in rows]

    def total_entities(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM entities").fetchone()
        return row["c"] if row else 0

    # ── Summary persistence (Faz 13-A) ───────────────────────────────────────

    def set_summary(self, session_id: str, summary: str, embedded_at: str | None = None) -> None:
        """Write the generated summary (and optional embed timestamp) to sessions."""
        with self._lock:
            self._conn.execute(
                "UPDATE sessions SET summary=?, summary_embedded_at=? WHERE id=?",
                (summary, embedded_at, session_id),
            )

    def sessions_needing_summary(self) -> list[dict]:
        """Return archived sessions that have no summary yet — backfill queue."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, topic_hint, last_active, message_count FROM sessions "
                "WHERE status='archived' AND (summary IS NULL OR summary='') "
                "AND message_count > 0 "
                "ORDER BY last_active DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def load_full_history(self, session_id: str) -> list[BaseMessage]:
        """Return the latest saved snapshot of messages for a session — used for summarization.

        See load_history(): only the MAX(turn_idx) bucket is read, since each
        bucket is a full cumulative snapshot rather than a per-turn delta.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM messages "
                "WHERE session_id=? AND turn_idx=("
                "  SELECT MAX(turn_idx) FROM messages WHERE session_id=?"
                ") ORDER BY msg_idx",
                (session_id, session_id),
            ).fetchall()
        msgs: list[BaseMessage] = []
        for row in rows:
            try:
                msgs.append(lc_loads(row["payload_json"]))
            except Exception:
                pass
        return msgs
