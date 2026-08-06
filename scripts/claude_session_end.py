"""SessionEnd hook -- leave a local breadcrumb when a session stops, however it stops.

Session Lifecycle v1. A session that ends by crash, timeout, `/quit` or a closed
terminal leaves nothing behind: the next session cannot tell "finished and closed
properly" from "died mid-edit with uncommitted work". This writes the few facts
needed to tell those apart, and nothing else.

It also **verifies** rather than assumes whose exit this is: the payload session
id is compared against the authoritative `current.json` written by SessionStart,
and the answer is recorded as `identity_status`. Without it, two records agreeing
on the same guessed id looked exactly like two records agreeing on the truth.

**It is deliberately the least-privileged hook in the system.** It writes exactly
one local, gitignored file. It does NOT:

  * touch HANDOFF.md, MEMORY.md, or any tracked file -- session CLOSING is a
    judgement call (what was verified? what is still open?) that belongs to
    /session-close with a human reading the result, not to a process that fires
    on every exit including a crash. A hook that rewrote HANDOFF on crash would
    overwrite a good handoff with a worse one at the worst possible moment.
  * commit, push, or write to any external system.
  * run tests.
  * delay or block the exit -- git calls are bounded hard, and every failure
    path is swallowed. A hook that can hang makes quitting the app feel broken.
  * copy transcript CONTENT or any secret. The transcript PATH is recorded (that
    is the recovery handle); its contents are never read.

Exit code is always 0: a non-zero exit here would surface an error to the owner
at the exact moment they are trying to leave, about a bookkeeping file that does
not affect their work.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

#: Shorter than the SessionStart budget on purpose -- this runs while the owner
#: is waiting for the app to close.
GIT_TIMEOUT_S = 3.0
MAX_DIRTY_NAMES = 20

RECOVERY_DIRNAME = Path(".claude") / "session-recovery"
LATEST_NAME = "latest.json"
MARKER_NAME = "close-marker.json"
CURRENT_NAME = "current.json"


def _git(cwd: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ("git", *args), cwd=cwd, capture_output=True, text=True,
            timeout=GIT_TIMEOUT_S, check=False,
        )
    except Exception:  # noqa: BLE001 -- never delay or fail the exit
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _dirty_names(cwd: str) -> list[str]:
    """Paths only -- `git status --porcelain` never emits file contents.

    Split rather than slice a fixed `XY ` prefix: _git strips the whole stdout,
    which drops the leading space of the first line only, so fixed slicing ate
    one character of exactly one filename (`.claude/...` -> `claude/...`).
    """
    porcelain = _git(cwd, "status", "--porcelain")
    if not porcelain:
        return []
    names = []
    for line in porcelain.splitlines():
        if not line.strip():
            continue
        parts = line.strip().split(None, 1)
        names.append(parts[1].strip() if len(parts) > 1 else line.strip())
    return names[:MAX_DIRTY_NAMES]


def _marker_state(root: Path) -> dict:
    """What /session-close last recorded, if anything.

    Read-only: this hook never writes the marker. `prepare`/`finalize` own it,
    so a crash can never fake a clean close.
    """
    path = root / RECOVERY_DIRNAME / MARKER_NAME
    if not path.exists():
        return {"state": "none"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"state": "unreadable"}
    return {
        "state": str(data.get("state") or "unknown"),
        "session_id": str(data.get("session_id") or ""),
        "prepared_at": str(data.get("prepared_at") or ""),
        "closed_at": str(data.get("closed_at") or ""),
    }


def _identity_status(root: Path, payload_session_id: str) -> str:
    """Does this exit belong to the session SessionStart authoritatively recorded?

    Three values, and only one of them may ever support a clean-close claim:

      * `matched`        -- the payload id equals `current.json`'s id.
      * `current_missing`-- there is no authoritative record to check against
                            (a session that pre-dates the mechanism, or a
                            SessionStart whose write failed).
      * `mismatch`       -- an id is present and is NOT the recorded one.

    The last two are deliberately not collapsed into one: "nobody wrote a
    record" and "the record disagrees" call for different reconciliation, and
    flattening them would hide the second behind the first. Neither is ever
    treated as evidence -- see the SessionStart preflight, which refuses to
    print `closed cleanly` on anything but `matched`.
    """
    path = root / RECOVERY_DIRNAME / CURRENT_NAME
    if not path.exists():
        return "current_missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "current_missing"
    if not isinstance(data, dict):
        return "current_missing"
    recorded = str(data.get("session_id") or "")
    if not recorded:
        return "current_missing"
    # NOTE: the payload id is compared, never substituted. Writing the recorded
    # id into `latest.json` when the payload disagrees would manufacture the
    # very agreement this field exists to measure.
    return "matched" if payload_session_id and payload_session_id == recorded else "mismatch"


def build_record(payload: dict, cwd: str) -> dict:
    root_raw = _git(cwd, "rev-parse", "--show-toplevel")
    root = Path(root_raw) if root_raw else Path(cwd)
    marker = _marker_state(root)
    session_id = str(payload.get("session_id") or "")
    return {
        "session_id": session_id,
        "identity_status": _identity_status(root, session_id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "reason": str(payload.get("reason") or "unknown"),
        # The PATH is the recovery handle. Contents are never read -- see the
        # module docstring.
        "transcript_path": str(payload.get("transcript_path") or ""),
        "branch": _git(cwd, "rev-parse", "--abbrev-ref", "HEAD") or "",
        "head": _git(cwd, "rev-parse", "HEAD") or "",
        "dirty_files": _dirty_names(cwd),
        "session_close_marker": marker,
        # Stated in the artifact itself so a reader who finds this file without
        # the docs knows it is not a close record.
        "note": ("written by the SessionEnd hook on every exit; not proof of a clean "
                 "close. Only identity_status == 'matched' can support one."),
    }


def main() -> int:
    payload: dict = {}
    try:
        raw = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        if raw.strip():
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = loaded
    except Exception:  # noqa: BLE001
        payload = {}

    cwd = str(payload.get("cwd") or os.getcwd())
    try:
        record = build_record(payload, cwd)
        root_raw = _git(cwd, "rev-parse", "--show-toplevel")
        root = Path(root_raw) if root_raw else Path(cwd)
        directory = root / RECOVERY_DIRNAME
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / LATEST_NAME
        # Atomic-ish: a killed process must not leave half a JSON file that the
        # next SessionStart then reports as "unreadable".
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except Exception:  # noqa: BLE001 -- bookkeeping never breaks the exit
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
