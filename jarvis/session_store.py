"""Persistent session store for JARVIS (Faz 12-B).

SQLite database at data/sessions.db with three tables:
  sessions  — one row per conversation session
  messages  — all LangChain messages serialized via langchain_core.load.dumps
  entities  — named entities extracted across all turns

Design decisions:
- WAL journal mode + autocommit for FastAPI concurrent access safety
- threading.Lock guards writes (single-process, multi-thread via FastAPI)
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
from typing import Any

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

    def _open(self, path: Path) -> sqlite3.Connection:
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
            return conn
        except sqlite3.DatabaseError:
            ts = datetime.now().strftime("%Y%m%d-%H%M%S")
            corrupt = path.with_suffix(f".db.corrupt-{ts}")
            path.rename(corrupt)
            print(f"[JARVIS] sessions.db was corrupt — renamed to {corrupt.name}, starting fresh.")
            conn = sqlite3.connect(
                str(path),
                check_same_thread=False,
                isolation_level=None,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
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
        row = self._conn.execute(
            "SELECT id FROM sessions WHERE status='active' "
            "ORDER BY last_active DESC LIMIT 1"
        ).fetchone()
        return row["id"] if row else None

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
        rows = self._conn.execute(
            "SELECT id, created_at, last_active, message_count, topic_hint, status "
            "FROM sessions ORDER BY last_active DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]

    def total_sessions(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()
        return row["c"] if row else 0

    # ── Message persistence ───────────────────────────────────────────────────

    def save_turn(
        self,
        session_id: str,
        messages: list[BaseMessage],
        turn_idx: int,
    ) -> None:
        ts = datetime.now().isoformat()
        with self._lock:
            # Remove existing rows for this turn (idempotent upsert)
            self._conn.execute(
                "DELETE FROM messages WHERE session_id=? AND turn_idx=?",
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

    def load_history(self, session_id: str, limit: int = 20) -> list[BaseMessage]:
        """Return the last `limit` non-system messages for a session."""
        rows = self._conn.execute(
            "SELECT payload_json FROM messages "
            "WHERE session_id=? "
            "ORDER BY turn_idx DESC, msg_idx DESC "
            "LIMIT ?",
            (session_id, limit * 4),  # overfetch: multiple messages per turn
        ).fetchall()

        messages: list[BaseMessage] = []
        for row in reversed(rows):
            try:
                msg = lc_loads(row["payload_json"])
                messages.append(msg)
            except Exception:
                pass

        from langchain_core.messages import SystemMessage
        non_system = [m for m in messages if not isinstance(m, SystemMessage)]
        return non_system[-limit:]

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
        rows = self._conn.execute(
            "SELECT name, type, description, mention_count, last_seen "
            "FROM entities ORDER BY mention_count DESC, last_seen DESC LIMIT ?",
            (n,),
        ).fetchall()
        return [dict(r) for r in rows]

    def search_entities(self, query: str) -> list[dict]:
        pattern = f"%{query}%"
        rows = self._conn.execute(
            "SELECT name, type, description, mention_count, last_seen "
            "FROM entities WHERE name LIKE ? OR description LIKE ? "
            "ORDER BY mention_count DESC LIMIT 10",
            (pattern, pattern),
        ).fetchall()
        return [dict(r) for r in rows]

    def total_entities(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM entities").fetchone()
        return row["c"] if row else 0
