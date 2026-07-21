"""Idempotency journal -- Agent Runtime rev.2, Faz 2 (plan section C).

"Idempotency burada, Faz E'de degil -- rev.1'in ic celismesi" -- the reviewer
caught that rev.1 claimed "duplicate side effect: 0" as an alpha invariant
while deferring the mechanism that would make it true to a much later phase.
This module is that mechanism, scoped narrowly and honestly:

What this catches: the SAME already-approved ExecutionRequest (same
execution_id) being handed to "tools" more than once -- e.g. a duplicated
Command(resume=...) delivery for one interrupt. confirmation_node checks
is_committed() right before its final approve (after a successful
approval.verify()), and tool_result_accounting calls commit() once a call
actually succeeds -- the only node that knows a real outcome happened.

What this does NOT catch (explicitly out of scope for Faz 2): two DIFFERENT
ExecutionRequests -- e.g. the model genuinely re-issuing "send this email"
in a later turn -- that happen to carry identical arguments. That is a
semantic-duplicate question ToolSpec.idempotency ("none" for every tool as
of Faz 1) is the declared future home for, once some tool is actually
classified "natural"/"keyed"; building that check now, with zero tools
classified, would have nothing real to exercise. The existing turn-scoped
seen/completed-fingerprint pre-gate (jarvis/graph/tool_accounting.py,
confirmation_node) already covers the narrower "identical call repeated
within one turn" case and is untouched by this module.

execution_id is minted fresh by prepare_execution on every single call
(nodes.py) -- never derived from the model's tool_call_id or reused across
turns -- so two independent requests can never collide here by construction;
only a genuine replay of one already-minted id can ever hit is_committed().

Storage shape deliberately mirrors jarvis/kill_switch.py and
jarvis/audit_log.py, not jarvis/procedure_store.py: resolve the path fresh
and open a short-lived connection per call, rather than caching a connection
tied to whatever cwd/JARVIS_HOME was active when a long-lived instance was
first constructed. A cached connection would be exactly the isolate-test-
data-paths bug class this project already hit once (see MEMORY.md) -- and
call volume here (a handful of rows per tool call) doesn't justify caching
against the cost of a fresh sqlite3.connect().
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from jarvis import paths

_lock = threading.Lock()

_TABLE = """
CREATE TABLE IF NOT EXISTS idempotency_journal (
    execution_id  TEXT PRIMARY KEY,
    capability    TEXT NOT NULL,
    inputs_digest TEXT NOT NULL,
    committed_at  TEXT NOT NULL
);
"""


def _path() -> Path:
    # Resolved per call, not at import -- JARVIS_HOME may be set by a test
    # fixture or the --profile test entry point after this module loads
    # (same reasoning as audit_log._path()/kill_switch._path()).
    return paths.data_dir() / "idempotency_journal.db"


def _connect() -> sqlite3.Connection:
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_TABLE)
    return conn


def is_committed(execution_id: str) -> bool:
    """True if this exact execution_id already had its side effect applied."""
    with _lock:
        conn = _connect()
        try:
            row = conn.execute(
                "SELECT 1 FROM idempotency_journal WHERE execution_id=?", (execution_id,)
            ).fetchone()
        finally:
            conn.close()
    return row is not None


def commit(execution_id: str, capability: str, inputs_digest: str) -> bool:
    """Record execution_id as committed. Returns True if this call actually
    recorded it (first commit for this id), False if it was already there --
    a redundant commit is a silent no-op, not a crash or a double-count, so a
    caller never needs to pre-check is_committed() before calling this."""
    with _lock:
        conn = _connect()
        try:
            cur = conn.execute(
                "INSERT OR IGNORE INTO idempotency_journal "
                "(execution_id, capability, inputs_digest, committed_at) VALUES (?,?,?,?)",
                (execution_id, capability, inputs_digest, datetime.now().isoformat()),
            )
        finally:
            conn.close()
    return cur.rowcount > 0
