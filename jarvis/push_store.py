"""FCM/APNs device token registry (Faz 19A-0).

Stores push tokens in the shared data/sessions.db.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path


_SCHEMA = """
CREATE TABLE IF NOT EXISTS push_tokens (
    device_id    TEXT PRIMARY KEY,
    token        TEXT NOT NULL,
    platform     TEXT NOT NULL DEFAULT 'android',
    registered_at TEXT NOT NULL,
    last_seen_at  TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


class PushStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        self._conn = conn

    def register(self, device_id: str, token: str, platform: str = "android") -> None:
        now = _now()
        with self._lock:
            self._conn.execute(
                """INSERT INTO push_tokens (device_id, token, platform, registered_at, last_seen_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(device_id) DO UPDATE SET
                     token = excluded.token,
                     platform = excluded.platform,
                     last_seen_at = excluded.last_seen_at""",
                (device_id, token, platform, now, now),
            )

    def unregister(self, device_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM push_tokens WHERE device_id = ?", (device_id,)
            )
        return cur.rowcount > 0

    def all_tokens(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM push_tokens").fetchall()
        return [dict(r) for r in rows]

    def remove_token(self, token: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM push_tokens WHERE token = ?", (token,))

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass
