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

Failure policy (2026-07-19 review hardening): a state file that EXISTS but
cannot be read or parsed is treated as TRIPPED — fail-closed — see _load().
A missing file stays the armed default; that's the legitimate fresh-install
state, not corruption.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from jarvis import audit_log, paths

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: dict[str, Any] | None = None
# One CRITICAL log per corruption episode, not per L3 call (policy_guard reads
# on every gated call). Re-armed by the next healthy read / missing file.
_unreadable_warned = False


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
    kept only as a fallback for a *missing* file after a healthy read, so
    deleting the file mid-run doesn't silently re-arm a live trip.

    Failure policy (2026-07-19): a state file that EXISTS but cannot be
    read/parsed (or parses without an "enabled" key) returns a synthetic
    TRIP -- fail-closed -- instead of silently falling back to the stale
    cache or the armed default. Both silent-fallback directions were the BOM
    incident's failure modes: an external writer's trip going invisible
    (emergency stop silently not stopped) and a stale trip shadowing a
    re-arm. An unreadable emergency stop must not quietly report "armed-off";
    a transient torn read at worst vetoes one L3 call in the safe direction.
    The synthetic trip is never written to _cache, so recovery is immediate
    once the file parses again (or /killswitch on|off rewrites it).
    """
    global _cache, _unreadable_warned
    target = _path()
    if target.exists():
        problem: Exception
        try:
            # utf-8-sig, not utf-8: reads plain UTF-8 unchanged AND tolerates a
            # BOM. Live incident (2026-07-18 A/B harness): a state file written
            # by PowerShell 5.1's `Out-File -Encoding utf8` carries a BOM and
            # json.loads() choked on it.
            loaded = json.loads(target.read_text(encoding="utf-8-sig"))
            if isinstance(loaded, dict) and "enabled" in loaded:
                _cache = loaded
                _unreadable_warned = False
                return _cache
            problem = ValueError("parsed JSON has no 'enabled' key")
        except Exception as exc:
            problem = exc
        return _fail_safe(target, problem)
    # No state file at all -- the legitimate fresh-install / manually-reset
    # state, not corruption. Any prior corruption episode is over.
    _unreadable_warned = False
    if _cache is not None:
        return _cache
    _cache = {"enabled": True, "reason": "", "changed_at": ""}
    return _cache


def _fail_safe(target: Path, problem: Exception) -> dict[str, Any]:
    """State file exists but can't be trusted: treat the switch as TRIPPED.

    Visible, not silent: CRITICAL log once per corruption episode plus a
    structured audit_log event. Returned WITHOUT touching _cache -- the last
    known-good state stays available for the missing-file path, and a fixed
    file takes effect on the very next call.
    """
    global _unreadable_warned
    detail = f"{type(problem).__name__}: {problem}"
    if not _unreadable_warned:
        _unreadable_warned = True
        logger.critical(
            "kill switch state file %s is unreadable (%s) -- failing SAFE: "
            "treating the switch as TRIPPED (all confirmable L3 actions "
            "blocked) until the file is fixed, rewritten via /killswitch, "
            "or deleted",
            target, detail,
        )
        # audit_log.record never raises (documented) -- safe on this hot path.
        audit_log.record(
            "kill_switch_state_unreadable",
            path=str(target), error=detail, action="fail_safe_trip",
        )
    return {
        "enabled": False,
        "reason": f"kill switch state unreadable -- failing safe ({detail})",
        "changed_at": "",
    }


def _save(state: dict[str, Any]) -> None:
    # Takes the state explicitly (not module _cache): _load() may have
    # returned a synthetic fail-safe dict that is deliberately NOT the cache,
    # and enable()/disable() must still persist what they actually mutated.
    global _cache
    _cache = state
    target = _path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


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
        state = dict(_load())
        state["enabled"] = False
        state["reason"] = reason_text
        state["changed_at"] = datetime.now().isoformat()
        _save(state)


def enable() -> None:
    """Re-arm. Also the operator's recovery path for a corrupt state file:
    _load() returns the synthetic trip, but the mutated copy written here is
    a fresh, valid file."""
    with _lock:
        state = dict(_load())
        state["enabled"] = True
        state["reason"] = ""
        state["changed_at"] = datetime.now().isoformat()
        _save(state)
