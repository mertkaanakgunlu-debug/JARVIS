"""Test-profile tool-call trace (Faz 2.2).

Every tool call — L1 reads included — as one JSON line in
``data/tool_trace.jsonl``, so the eval oracle can verify the RIGHT tool ran
with the RIGHT args and actually succeeded. The audit log only records
risk_level >= 2, so on its own it cannot prove an L1 ``web_search`` /
``file_read`` / ``url_read`` happened or didn't — the exact gap behind the
"qwen2.5 made zero real tool calls" claim, which was strong evidence for L2/L3
writes but unprovable for L1 reads.

Gated by the ``JARVIS_TOOL_TRACE`` env var (set only by ``--profile test``), so
production never writes it. Mirrors audit_log's robustness: resolved per call
(JARVIS_HOME may be set after import), append-only, never raises.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis import paths

_lock = threading.Lock()


def is_enabled() -> bool:
    return os.environ.get("JARVIS_TOOL_TRACE") == "1"


def _path() -> Path:
    return paths.data_dir() / "tool_trace.jsonl"


def record(**fields: Any) -> None:
    """Append one trace entry (no-op unless enabled). Never raises."""
    if not is_enabled():
        return
    entry = {"ts": datetime.now().isoformat()}
    entry.update(fields)
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


def load(data_dir: Path | None = None) -> list[dict]:
    """Read the trace (newest last). ``data_dir`` lets the harness read another
    process's JARVIS_HOME/data; default resolves the current process's home."""
    target = (Path(data_dir) / "tool_trace.jsonl") if data_dir else _path()
    if not target.exists():
        return []
    out: list[dict] = []
    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            continue
    return out


def clear(data_dir: Path | None = None) -> None:
    """Truncate the trace for a clean per-scenario read. The harness calls this
    by path (it drives a separate server process, so it can't use the in-process
    default)."""
    target = (Path(data_dir) / "tool_trace.jsonl") if data_dir else _path()
    try:
        target.unlink(missing_ok=True)
    except Exception:
        pass
