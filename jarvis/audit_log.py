"""Append-only audit trail for every side-effecting tool call (Faz 4).

One JSON object per line, `data/audit_log.jsonl` — deliberately not SQLite: an
audit log's core property is "never silently overwritten", and a straight-
append text file is the simplest thing that can't accidentally lose that
property to a bug in some future UPDATE/DELETE statement elsewhere. Nothing
in this module ever truncates or rewrites the file; rotation/pruning is an
operator's manual choice.

Two event kinds, both recorded through the same record() call:
  "decision"   — policy_guard's gate ruling on a call BEFORE it runs
                 (auto_approved / confirm_required / user_approved /
                 user_denied / blocked_kill_switch)
  "execution"  — the LangChain tool-callback hook recording that a
                 side-effecting call actually ran, and whether it succeeded
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis import paths

_lock = threading.Lock()


def _path() -> Path:
    # Resolved per call, not at import: JARVIS_HOME may be set by a test
    # fixture or the --profile test entry point after this module loads.
    return paths.data_dir() / "audit_log.jsonl"

_MAX_FIELD_CHARS = 500


def _truncate(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _MAX_FIELD_CHARS:
        return value[:_MAX_FIELD_CHARS] + f"...[+{len(value) - _MAX_FIELD_CHARS} chars]"
    return value


def record(event: str, **fields: Any) -> None:
    """Append one audit entry. event: 'decision' | 'execution_start' |
    'execution_end'. Never raises — a logging failure must not break the
    call it's trying to record."""
    entry = {"ts": datetime.now().isoformat(), "event": event}
    entry.update({k: _truncate(v) for k, v in fields.items()})
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str)
    except Exception:
        return
    try:
        with _lock:
            target = _path()
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


def tail(n: int = 20) -> list[dict[str, Any]]:
    """Return the last n audit entries, newest last (debugging / a future
    /audit CLI command — not required for the safety guarantee itself, which
    is the append-only file regardless of whether anything ever reads it)."""
    target = _path()
    if not target.exists():
        return []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out
