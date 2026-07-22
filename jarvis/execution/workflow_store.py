"""WorkflowPlan persistence -- Agent Runtime rev.2, Faz 7 (checkpoint/resume).

Storage shape deliberately mirrors jarvis.execution.idempotency (and, one
level further back, jarvis/kill_switch.py / jarvis/audit_log.py): resolve
the path fresh and open a short-lived connection per call, rather than
caching a connection tied to whatever cwd/JARVIS_HOME was active when a
long-lived instance was first constructed -- the isolate-test-data-paths bug
class this project already hit once (see MEMORY.md).

The whole plan is stored as one JSON blob per row (via WorkflowPlan's own
pydantic serialization) rather than normalized across tables -- a workflow
is read/written as a single unit by WorkflowEngine (checkpoint after every
step, load whole on resume), so there is no query pattern here that would
benefit from column-level access, and a single blob means WorkflowStep's
shape can evolve without a migration.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from jarvis import paths
from jarvis.execution.workflow import WorkflowPlan

_lock = threading.Lock()

_TABLE = """
CREATE TABLE IF NOT EXISTS workflows (
    workflow_id TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    plan_json   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
"""


def _path() -> Path:
    return paths.data_dir() / "workflows.db"


def _connect() -> sqlite3.Connection:
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_TABLE)
    return conn


def save(plan: WorkflowPlan) -> None:
    """Insert or fully overwrite this workflow_id's row. Called by
    WorkflowEngine after every state-changing step (create, dispatch,
    approval pause/resume, replan, compensate) -- the checkpoint this
    phase's "resume" requirement depends on."""
    plan.updated_at = datetime.now(timezone.utc).isoformat()
    payload = plan.model_dump_json()
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO workflows (workflow_id, status, plan_json, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(workflow_id) DO UPDATE SET "
                "status=excluded.status, plan_json=excluded.plan_json, updated_at=excluded.updated_at",
                (plan.workflow_id, plan.status, payload, plan.created_at, plan.updated_at),
            )
        finally:
            conn.close()


def load(workflow_id: str) -> WorkflowPlan | None:
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT plan_json FROM workflows WHERE workflow_id = ?", (workflow_id,)
            ).fetchone()
        finally:
            conn.close()
    if row is None:
        return None
    return WorkflowPlan.model_validate_json(row[0])


def list_workflows(status: str | None = None, limit: int = 50) -> list[dict]:
    """Light listing (id/status/timestamps only, no full plan_json) --
    mirrors SessionStore.list_sessions()'s shape for the same reason: a
    caller browsing workflows shouldn't have to deserialize every plan just
    to show a picker."""
    with _lock:
        conn = _connect()
        try:
            if status is not None:
                rows = conn.execute(
                    "SELECT workflow_id, status, created_at, updated_at FROM workflows "
                    "WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT workflow_id, status, created_at, updated_at FROM workflows "
                    "ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        finally:
            conn.close()
    return [
        {"workflow_id": r[0], "status": r[1], "created_at": r[2], "updated_at": r[3]}
        for r in rows
    ]


def delete(workflow_id: str) -> bool:
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM workflows WHERE workflow_id = ?", (workflow_id,))
        finally:
            conn.close()
    return cur.rowcount > 0
