"""The single write API for machine-authored session state under `.claude/session-recovery/`.

Session Lifecycle v1 shipped with one identity hole, found in a real CLI
acceptance run: `/session-close` wrote the recovery marker as **model-authored
JSON**, so the `session_id` in it was whatever the model believed the session was
called. A model cannot observe its own session id -- it can only infer one from a
transcript filename, from "the newest file in the directory", or from a guess.
Every one of those is a plausible-looking value that is not evidence, and a
marker carrying a guessed id certifies the wrong session: the next SessionStart
compares marker id against the previous SessionEnd's id, so a wrong-but-similar
id reads as a clean close of a session that never closed.

This module removes the guess by removing the choice. The only value that is ever
written as a session identity is the one Claude Code itself puts in the
SessionStart hook payload, recorded once in `current.json`; every later state
transition reads its identity from that file and refuses to accept one as an
argument. There is no code path here that derives a session id from a filename,
a directory listing, a transcript, or a command-line flag.

Commands::

    current   record the authoritative session identity (SessionStart's job)
    prepare   mark the session prepared-to-close  (reads identity from current)
    close     mark it closed                      (only from `prepared`)
    block     mark it blocked by CI               (only from `prepared`/`blocked`)
    show      print a redacted summary of both files

What it deliberately does NOT do:

  * **no session-id inference, ever** -- see above. `prepare`/`close`/`block`
    take no id argument at all, so "type the id by hand" is not a mistake that
    can be made.
  * **no CI judgement.** Whether a red job is blocking is a classification with
    real context behind it (does the diff touch `mobile/**`? is it the known
    ChromaDB flake?) and belongs to the `/session-close` skill with a human
    reading the result. This module's whole responsibility is identity and
    state-transition integrity: it will happily record a `blocked` marker, and
    it will never decide that one is warranted.
  * **no network, no push, no tracked-file write.** It writes exactly two local,
    gitignored files.
  * **no absolute user paths, no transcript path, no free-form text.** Everything
    written is either derived from git or validated against a narrow pattern, so
    a secret has no field to land in.

Exit codes: 0 on success, non-zero on a refused transition. Refusal is the point
-- unlike the hooks, this is invoked deliberately and a silent failure here would
let a session close on a broken identity.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1

RECOVERY_DIRNAME = Path(".claude") / "session-recovery"
CURRENT_NAME = "current.json"
MARKER_NAME = "close-marker.json"

#: Bounded like every other git call in this system: a hung git must cost a few
#: seconds, never the session.
GIT_TIMEOUT_S = 5.0

#: The four `source` values Claude Code emits for SessionStart. An unrecognised
#: source is NOT recorded -- writing an unknown string into the identity record
#: would make the record's own provenance unverifiable.
KNOWN_SOURCES = frozenset({"startup", "resume", "clear", "compact"})

#: Claude Code session ids are UUIDs. The pattern is deliberately narrower than
#: "any string": it structurally excludes a path, a shell fragment, or a
#: multi-line blob ever being stored as an identity.
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: `block` metadata is a fixed, validated vocabulary rather than free text, so
#: there is no field an absolute path or a secret could be smuggled through.
_REASON_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_RUN_ID_RE = re.compile(r"^[0-9]{1,32}$")
_JOB_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}$")


class StateError(Exception):
    """A refused transition. The message is user-facing and carries no paths."""


# ── git ─────────────────────────────────────────────────────────────────────

def _git(cwd: str, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ("git", *args), cwd=cwd, capture_output=True, text=True,
            timeout=GIT_TIMEOUT_S, check=False,
        )
    except Exception:  # noqa: BLE001 -- callers decide whether this is fatal
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def repo_root(cwd: str) -> Path:
    toplevel = _git(cwd, "rev-parse", "--show-toplevel")
    if not toplevel:
        raise StateError("not inside a git repository")
    return Path(toplevel)


def derive_branch_and_head(cwd: str) -> tuple[str, str]:
    """Branch and full HEAD SHA, measured now -- never taken from an argument."""
    branch = _git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    head = _git(cwd, "rev-parse", "HEAD")
    if not branch or not head:
        raise StateError("could not derive branch/HEAD from git")
    return branch, head


def _is_ancestor(cwd: str, older: str, newer: str) -> bool:
    return _git(cwd, "merge-base", "--is-ancestor", older, newer) is not None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── file io ─────────────────────────────────────────────────────────────────

def _atomic_write_json(path: Path, data: dict) -> None:
    """tmp + os.replace, so a killed process never leaves half a JSON file.

    A truncated record is worse than a missing one: the reader reports it as
    `unreadable`, which is a different and less actionable verdict than `absent`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink()
        except Exception:  # noqa: BLE001 -- best effort; the raise below is what matters
            pass
        raise


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return loaded if isinstance(loaded, dict) else None


def current_path(root: Path) -> Path:
    return root / RECOVERY_DIRNAME / CURRENT_NAME


def marker_path(root: Path) -> Path:
    return root / RECOVERY_DIRNAME / MARKER_NAME


# ── current.json -- the authoritative identity ──────────────────────────────

def write_current(cwd: str, session_id: str, source: str) -> tuple[bool, str]:
    """Record the session identity Claude Code reported. Called by SessionStart.

    Returns `(ok, detail)` rather than raising: the caller is a fail-open hook,
    and a session must start even when its bookkeeping cannot be written.

    Every rejection leaves any existing `current.json` **untouched**. A payload
    that arrives without an id is a degraded start, not a reason to overwrite a
    good record with an empty one.
    """
    if source not in KNOWN_SOURCES:
        return False, f"unrecognised source {source!r}"
    session_id = (session_id or "").strip()
    if not session_id:
        return False, "hook payload carried no session_id"
    if not _SESSION_ID_RE.match(session_id):
        return False, "session_id is not a plain identifier"
    try:
        root = repo_root(cwd)
        branch, head = derive_branch_and_head(cwd)
        _atomic_write_json(current_path(root), {
            "schema": SCHEMA_VERSION,
            "session_id": session_id,
            "source": source,
            "updated_at": _now(),
            "branch": branch,
            "head": head,
        })
    except StateError as exc:
        return False, str(exc)
    except Exception as exc:  # noqa: BLE001 -- bookkeeping never blocks a start
        return False, f"write failed ({type(exc).__name__})"
    return True, "written"


def load_current(root: Path) -> dict:
    """The authoritative identity, or a refusal explaining why there is none."""
    data = _read_json(current_path(root))
    if data is None:
        raise StateError(
            "no authoritative current-session record "
            "(.claude/session-recovery/current.json is missing or unreadable); "
            "it is written by the SessionStart hook -- do not create one by hand"
        )
    if data.get("schema") != SCHEMA_VERSION:
        raise StateError(f"current-session record has schema {data.get('schema')!r}, "
                         f"expected {SCHEMA_VERSION}")
    session_id = str(data.get("session_id") or "")
    if not _SESSION_ID_RE.match(session_id):
        raise StateError("current-session record carries no usable session_id")
    if not str(data.get("branch") or ""):
        raise StateError("current-session record carries no branch")
    if not _FULL_SHA_RE.match(str(data.get("head") or "")):
        raise StateError("current-session record carries no full HEAD sha")
    return data


# ── transitions ─────────────────────────────────────────────────────────────

def _consistency_check(cwd: str, current: dict) -> tuple[str, str]:
    """Is the repo still the one this session's identity was recorded against?

    Two different checks, because the two facts age differently:

    * **branch is compared for equality.** A session that started on one branch
      and is closing on another is not a state this protocol models, and a
      marker written from it would describe work the branch does not contain.
    * **HEAD is compared by ancestry, not equality.** HEAD is *expected* to move
      during a session -- that is what the work commits are. Demanding equality
      would make `prepare` fail on every session that committed anything, which
      is every session that has something to close. What must hold is that the
      history the identity was recorded on is still reachable: a rewrite
      (rebase, reset, amend of an earlier commit) means the recorded head is no
      longer ancestral, and that record can no longer vouch for this tree.
    """
    branch, head = derive_branch_and_head(cwd)
    recorded_branch = str(current.get("branch") or "")
    recorded_head = str(current.get("head") or "")
    if recorded_branch != branch:
        raise StateError(f"session was recorded on branch {recorded_branch!r} "
                         f"but the repository is on {branch!r}")
    if not _is_ancestor(cwd, recorded_head, head):
        raise StateError(f"the session's recorded HEAD {recorded_head[:8]} is no longer an "
                         "ancestor of HEAD -- history was rewritten under this session")
    return branch, head


def _require_marker_identity(marker: dict, current: dict, branch: str, head: str,
                             allowed_states: tuple[str, ...]) -> None:
    """The guard that makes a marker non-transferable between sessions."""
    state = str(marker.get("state") or "")
    if state not in allowed_states:
        raise StateError(f"marker state is {state!r}; expected one of "
                         f"{', '.join(repr(s) for s in allowed_states)}")
    if str(marker.get("session_id") or "") != str(current.get("session_id") or ""):
        raise StateError("the marker belongs to a different session than the current one")
    if str(marker.get("head") or "") != head:
        raise StateError(f"the marker was written at {str(marker.get('head') or '?')[:8]} "
                         f"but HEAD is now {head[:8]} -- re-run prepare")
    if str(marker.get("branch") or "") != branch:
        raise StateError("the marker was written on a different branch")


def _load_marker(root: Path) -> dict:
    marker = _read_json(marker_path(root))
    if marker is None:
        raise StateError("no close marker exists -- run `prepare` first")
    return marker


def cmd_prepare(cwd: str) -> dict:
    """Mark the session prepared-to-close. Identity comes only from current.json."""
    root = repo_root(cwd)
    current = load_current(root)
    branch, head = _consistency_check(cwd, current)
    marker = {
        "schema": SCHEMA_VERSION,
        "state": "prepared",
        "session_id": current["session_id"],
        "prepared_at": _now(),
        "head": head,
        "branch": branch,
    }
    _atomic_write_json(marker_path(root), marker)
    return marker


def cmd_close(cwd: str) -> dict:
    """`prepared` -> `closed`. Re-running on an already-closed marker is a no-op.

    Deliberately NOT reachable from `blocked`: a blocked session's next honest
    step is to fix the failure and prepare again, not to relabel the marker.
    """
    root = repo_root(cwd)
    current = load_current(root)
    branch, head = _consistency_check(cwd, current)
    marker = _load_marker(root)
    _require_marker_identity(marker, current, branch, head, ("prepared", "closed"))
    closed = {
        "schema": SCHEMA_VERSION,
        "state": "closed",
        "session_id": current["session_id"],
        "prepared_at": str(marker.get("prepared_at") or ""),
        # Idempotent: the FIRST close is the one that happened.
        "closed_at": str(marker.get("closed_at") or "") or _now(),
        "head": head,
        "branch": branch,
    }
    _atomic_write_json(marker_path(root), closed)
    return closed


def _validated_block_metadata(reason_code: str, run_id: str | None,
                              blocking_jobs: list[str]) -> dict:
    if not _REASON_CODE_RE.match(reason_code or ""):
        raise StateError("reason_code must be an UPPER_SNAKE code, e.g. CI_BLOCKING_FAILURE")
    if run_id is not None and not _RUN_ID_RE.match(run_id):
        raise StateError("run_id must be the numeric CI run id")
    for job in blocking_jobs:
        if not _JOB_NAME_RE.match(job):
            raise StateError(f"blocking job name {job!r} is not a plain job name")
    meta: dict = {"reason_code": reason_code, "blocking_jobs": list(blocking_jobs)}
    if run_id is not None:
        meta["run_id"] = run_id
    return meta


def cmd_block(cwd: str, reason_code: str, run_id: str | None,
              blocking_jobs: list[str]) -> dict:
    """`prepared` -> `blocked`. Re-blocking refreshes the metadata."""
    root = repo_root(cwd)
    current = load_current(root)
    branch, head = _consistency_check(cwd, current)
    marker = _load_marker(root)
    _require_marker_identity(marker, current, branch, head, ("prepared", "blocked"))
    meta = _validated_block_metadata(reason_code, run_id, blocking_jobs)
    blocked = {
        "schema": SCHEMA_VERSION,
        "state": "blocked",
        "session_id": current["session_id"],
        "prepared_at": str(marker.get("prepared_at") or ""),
        "blocked_at": _now(),
        "head": head,
        "branch": branch,
        **meta,
    }
    _atomic_write_json(marker_path(root), blocked)
    return blocked


# ── show ────────────────────────────────────────────────────────────────────

def _short_id(value: str) -> str:
    value = str(value or "")
    return f"{value[:8]}..." if len(value) > 8 else (value or "-")


def render_show(root: Path) -> str:
    """A redacted summary. Session ids and SHAs are truncated; no path is printed."""
    current = _read_json(current_path(root))
    marker = _read_json(marker_path(root))
    lines = []
    if current is None:
        lines.append("current   absent or unreadable (SessionStart has not recorded one)")
    else:
        lines.append(f"current   session {_short_id(current.get('session_id'))}  "
                     f"source {current.get('source') or '?'}  "
                     f"updated {current.get('updated_at') or '?'}")
        lines.append(f"          branch {current.get('branch') or '?'}  "
                     f"head {_short_id(current.get('head'))}")
    if marker is None:
        lines.append("marker    absent or unreadable")
    else:
        stamps = " ".join(f"{key} {marker[key]}" for key in
                          ("prepared_at", "closed_at", "blocked_at")
                          if str(marker.get(key) or ""))
        lines.append(f"marker    state {marker.get('state') or '?'}  "
                     f"session {_short_id(marker.get('session_id'))}")
        lines.append(f"          branch {marker.get('branch') or '?'}  "
                     f"head {_short_id(marker.get('head'))}"
                     + (f"  {stamps}" if stamps else ""))
        if str(marker.get("state") or "") == "blocked":
            jobs = marker.get("blocking_jobs")
            names = ", ".join(str(j) for j in jobs) if isinstance(jobs, list) else "?"
            lines.append(f"          reason {marker.get('reason_code') or '?'}  "
                         f"run {marker.get('run_id') or '-'}  jobs {names}")
    return "\n".join(lines)


# ── cli ─────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude_session_state",
        description="Machine-authored session state for .claude/session-recovery/.",
    )
    parser.add_argument("--cwd", default=None,
                        help="repository directory (default: the working directory)")
    sub = parser.add_subparsers(dest="command", required=True)

    # NOTE: `current` is the SessionStart hook's command. It is the ONLY
    # subcommand that accepts a session id, because it is the only one with an
    # authoritative one to record. Running it by hand to invent an identity is
    # forbidden by the /session-close skill.
    p_current = sub.add_parser("current", help="record the authoritative session identity")
    p_current.add_argument("--session-id", required=True,
                           help="the id from the SessionStart hook payload -- never a guess")
    p_current.add_argument("--source", required=True, choices=sorted(KNOWN_SOURCES))

    # No `--session-id` on any transition, by design: an identity that can be
    # passed in is an identity that can be guessed.
    sub.add_parser("prepare", help="mark the session prepared to close")
    sub.add_parser("close", help="mark the prepared session closed")

    p_block = sub.add_parser("block", help="mark the prepared session blocked")
    p_block.add_argument("--reason-code", default="CI_BLOCKING_FAILURE")
    p_block.add_argument("--run-id", default=None)
    p_block.add_argument("--blocking-jobs", default="",
                         help="comma-separated job names, e.g. python,electron")

    sub.add_parser("show", help="print a redacted summary of the session state")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cwd = args.cwd or os.getcwd()
    try:
        if args.command == "current":
            ok, detail = write_current(cwd, args.session_id, args.source)
            if not ok:
                sys.stderr.write(f"refused: {detail}\n")
                return 2
            sys.stdout.write(render_show(repo_root(cwd)) + "\n")
            return 0
        if args.command == "show":
            sys.stdout.write(render_show(repo_root(cwd)) + "\n")
            return 0
        if args.command == "prepare":
            cmd_prepare(cwd)
        elif args.command == "close":
            cmd_close(cwd)
        elif args.command == "block":
            jobs = [j.strip() for j in str(args.blocking_jobs).split(",") if j.strip()]
            cmd_block(cwd, args.reason_code, args.run_id, jobs)
        sys.stdout.write(render_show(repo_root(cwd)) + "\n")
    except StateError as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
