"""Kill switch — a persisted, process-independent hard stop for side-effecting
tool calls (Faz 4).

"Off" is the safety-triggered state: when disabled, policy_guard vetoes every
call it would otherwise gate behind a confirmation prompt, with no prompt at
all — asking permission is pointless once the operator has already said stop.
Defaults to enabled=True so installing this ships with no behavior change
until someone deliberately trips it.

Persisted to a small JSON file (not a Settings field) so a trip survives a
process restart — the whole point of an emergency stop is that it stays
stopped until someone deliberately re-arms it, not until the next reboot.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis import paths

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def _path() -> Path:
    # Resolved per call so JARVIS_HOME set after import still takes effect.
    return paths.data_dir() / "kill_switch.json"


def _load() -> dict[str, Any]:
    """Always re-read from disk -- deliberately not cached across calls.

    The original design cached the first successful read for the rest of the
    process's life. is_enabled() is checked on every L3 tool call specifically
    so a trip takes effect immediately -- but a load-once cache meant a trip
    from one process (e.g. the CLI's /killswitch) was invisible to any other
    already-running process (e.g. a long-lived `--api --monitor` server)
    until that process restarted, silently defeating the "hard stop, no
    prompt" guarantee in exactly the deployment shape this project targets.
    The file is a few bytes and the only caller (policy_guard, gated behind
    an L3 tool call) is already about to do far more expensive work, so
    re-reading it every time costs nothing worth caching against. _cache is
    kept only as a last-resort fallback for a transient read failure, not as
    a steady-state optimization.
    """
    global _cache
    target = _path()
    if target.exists():
        try:
            loaded = json.loads(target.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and "enabled" in loaded:
                _cache = loaded
                return _cache
        except Exception:
            pass
    if _cache is not None:
        return _cache
    _cache = {"enabled": True, "reason": "", "changed_at": ""}
    return _cache


def _save() -> None:
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")


def is_enabled() -> bool:
    with _lock:
        return bool(_load().get("enabled", True))


def reason() -> str:
    with _lock:
        return str(_load().get("reason", ""))


def status() -> dict[str, Any]:
    with _lock:
        return dict(_load())


def disable(reason_text: str = "") -> None:
    """Trip the switch — blocks every confirmable (risk_level >= 3) tool call
    outright, with no prompt, until enable() is called."""
    with _lock:
        state = _load()
        state["enabled"] = False
        state["reason"] = reason_text
        state["changed_at"] = datetime.now().isoformat()
        _save()


def enable() -> None:
    with _lock:
        state = _load()
        state["enabled"] = True
        state["reason"] = ""
        state["changed_at"] = datetime.now().isoformat()
        _save()
