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

_PATH = Path("data") / "kill_switch.json"
_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    if _PATH.exists():
        try:
            loaded = json.loads(_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and "enabled" in loaded:
                _cache = loaded
                return _cache
        except Exception:
            pass
    _cache = {"enabled": True, "reason": "", "changed_at": ""}
    return _cache


def _save() -> None:
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(_cache, indent=2, ensure_ascii=False), encoding="utf-8")


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
