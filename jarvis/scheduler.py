"""Scheduled tasks & reminders engine for JARVIS (Faz 13-C).

Stores tasks in sessions.db (scheduled_tasks table).
Supports one-time and recurring (daily / weekly / monthly) schedules.
The monitor daemon calls check_due() on a configurable interval.

Usage:
    store = SchedulerStore(db_path)
    task_id = store.add_task("Kahve molası", schedule_type="daily", run_at="14:30")
    due = store.check_due()          # returns tasks that should fire NOW
    store.mark_ran(task_id)          # updates last_run + calculates next_run
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from jarvis.clock import local_naive_now


_SCHEDULE_TABLE = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT,
    schedule_type    TEXT NOT NULL DEFAULT 'once',
    run_at           TEXT NOT NULL,
    days_of_week     TEXT,
    day_of_month     INTEGER,
    last_run         TEXT,
    next_run         TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'active',
    created_at       TEXT NOT NULL,
    notify_before_min INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_sched_next ON scheduled_tasks(next_run, status);
"""

# schedule_type values
ONCE    = "once"
DAILY   = "daily"
WEEKLY  = "weekly"
MONTHLY = "monthly"


def _now_iso() -> str:
    return local_naive_now().isoformat(timespec="seconds")


def _parse_time(run_at: str) -> tuple[int, int]:
    """Parse HH:MM → (hour, minute). Raises ValueError on bad format."""
    try:
        h, m = run_at.split(":")
        return int(h), int(m)
    except Exception:
        raise ValueError(f"run_at must be HH:MM or ISO datetime, got: {run_at!r}")


def calc_next_run(
    schedule_type: str,
    run_at: str,
    days_of_week: list[int] | None = None,
    day_of_month: int | None = None,
    after: datetime | None = None,
) -> str:
    """Calculate the next ISO datetime when this task should fire.

    Args:
        schedule_type: 'once' | 'daily' | 'weekly' | 'monthly'
        run_at:        ISO datetime for 'once'; 'HH:MM' for recurring
        days_of_week:  [0–6] Mon=0 … Sun=6 (used when schedule_type='weekly')
        day_of_month:  1–31 (used when schedule_type='monthly')
        after:         calculate next occurrence after this moment (default: now)

    Post-MVP Faz 2: "now" comes from jarvis.clock, i.e. the CONFIGURED
    timezone, not the operating system's. A reminder set for 14:30 should fire
    at 14:30 in the zone the rest of JARVIS thinks it lives in.
    """
    now = after or local_naive_now()

    if schedule_type == ONCE:
        # run_at is a full ISO datetime string
        try:
            dt = datetime.fromisoformat(run_at)
        except ValueError:
            # Maybe it's just a date
            dt = datetime.fromisoformat(run_at + "T00:00:00")
        return dt.isoformat(timespec="seconds")

    hour, minute = _parse_time(run_at)

    if schedule_type == DAILY:
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate.isoformat(timespec="seconds")

    if schedule_type == WEEKLY:
        dow_list = days_of_week or [0]  # default: Monday
        today_dow = now.weekday()  # Mon=0 … Sun=6
        days_ahead_options = []
        for target_dow in dow_list:
            delta = (target_dow - today_dow) % 7
            if delta == 0:
                # Same weekday — check if time has passed
                candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if candidate > now:
                    days_ahead_options.append(0)
                else:
                    days_ahead_options.append(7)
            else:
                days_ahead_options.append(delta)
        days_ahead = min(days_ahead_options)
        candidate = (now + timedelta(days=days_ahead)).replace(
            hour=hour, minute=minute, second=0, microsecond=0
        )
        return candidate.isoformat(timespec="seconds")

    if schedule_type == MONTHLY:
        dom = day_of_month or 1
        candidate = now.replace(day=dom, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            # Next month
            if now.month == 12:
                candidate = candidate.replace(year=now.year + 1, month=1)
            else:
                candidate = candidate.replace(month=now.month + 1)
        return candidate.isoformat(timespec="seconds")

    raise ValueError(f"Unknown schedule_type: {schedule_type!r}")


class SchedulerStore:
    """Thread-safe SQLite store for scheduled tasks.

    Designed to share the same SQLite file as SessionStore (sessions.db)
    but can be opened with any path.  Migration is idempotent.
    """

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = self._open(db_path)

    # ── connection ──────────────────────────────────────────────────────────

    @staticmethod
    def _open(db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEDULE_TABLE)
        return conn

    # ── CRUD ────────────────────────────────────────────────────────────────

    def add_task(
        self,
        title: str,
        *,
        schedule_type: str = DAILY,
        run_at: str,
        description: str = "",
        days_of_week: list[int] | None = None,
        day_of_month: int | None = None,
        notify_before_min: int = 0,
    ) -> str:
        """Create a new scheduled task. Returns the new task ID."""
        task_id = str(uuid.uuid4())[:8]
        next_run = calc_next_run(
            schedule_type,
            run_at,
            days_of_week=days_of_week,
            day_of_month=day_of_month,
        )
        dow_json = json.dumps(days_of_week) if days_of_week is not None else None
        with self._lock:
            self._conn.execute(
                """INSERT INTO scheduled_tasks
                   (id, title, description, schedule_type, run_at,
                    days_of_week, day_of_month, next_run, created_at, notify_before_min)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id, title, description, schedule_type, run_at,
                    dow_json, day_of_month, next_run, _now_iso(), notify_before_min,
                ),
            )
        return task_id

    def list_tasks(self, status: str = "active") -> list[dict[str, Any]]:
        """Return tasks filtered by status, ordered by next_run."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scheduled_tasks WHERE status=? ORDER BY next_run",
                (status,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM scheduled_tasks WHERE id=?", (task_id,)
            ).fetchone()
        return dict(row) if row else None

    def delete_task(self, task_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM scheduled_tasks WHERE id=?", (task_id,)
            )
        return cur.rowcount > 0

    def pause_task(self, task_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE scheduled_tasks SET status='paused' WHERE id=?", (task_id,)
            )
        return cur.rowcount > 0

    def resume_task(self, task_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE scheduled_tasks SET status='active' WHERE id=?", (task_id,)
            )
        return cur.rowcount > 0

    def mark_ran(self, task_id: str) -> None:
        """Update last_run and calculate next_run; mark 'once' tasks as done."""
        task = self.get_task(task_id)
        if not task:
            return
        now_str = _now_iso()
        if task["schedule_type"] == ONCE:
            with self._lock:
                self._conn.execute(
                    "UPDATE scheduled_tasks SET last_run=?, status='done' WHERE id=?",
                    (now_str, task_id),
                )
        else:
            # Recalculate from now (avoid drift)
            dow = json.loads(task["days_of_week"]) if task["days_of_week"] else None
            next_r = calc_next_run(
                task["schedule_type"],
                task["run_at"],
                days_of_week=dow,
                day_of_month=task["day_of_month"],
                after=local_naive_now(),
            )
            with self._lock:
                self._conn.execute(
                    "UPDATE scheduled_tasks SET last_run=?, next_run=? WHERE id=?",
                    (now_str, next_r, task_id),
                )

    def check_due(self, window_sec: int = 90) -> list[dict[str, Any]]:
        """Return active tasks whose next_run is within the next `window_sec` seconds
        (or already past). Does NOT mark them as ran — caller must call mark_ran()."""
        now = local_naive_now()
        cutoff = (now + timedelta(seconds=window_sec)).isoformat(timespec="seconds")
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM scheduled_tasks
                   WHERE status='active' AND next_run <= ?
                   ORDER BY next_run""",
                (cutoff,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_active(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM scheduled_tasks WHERE status='active'"
            ).fetchone()[0]

    def close(self) -> None:
        """Close the SQLite connection (call before process exit or in tests)."""
        try:
            self._conn.close()
        except Exception:
            pass
