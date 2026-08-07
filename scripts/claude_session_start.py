"""SessionStart hook -- the repository preflight a session used to be handed by hand.

Session Lifecycle v1. Before this, every Claude Code session began with the owner
pasting a long orientation prompt, and the parts that actually change between
sessions (which commit, which branch, is anything uncommitted, did the last
session close) were exactly the parts a human had to re-type and could get wrong.
This emits them, measured, at session start.

**Fail-open is the whole design constraint.** A preflight that can stop a session
from starting is worse than no preflight: the owner would be locked out of their
own repo by a broken helper. So every failure path here degrades to a shorter
context block and exit 0 -- never a non-zero exit, never a raised exception, never
a blocked start. When something could not be read, the block says
`SESSION PREFLIGHT DEGRADED` and names what failed, because a preflight that
silently omits a check reads identically to one that ran and found nothing wrong.

What it deliberately does NOT do:

  * no fetch by default -- session start must not wait on the network, and a
    stale-by-minutes origin ref is not worth a hang. The block SAYS the ref is
    the local cached one. Opt in with JARVIS_SESSION_START_FETCH=1 (bounded by
    the same short timeout as every other git call here).
  * SessionStart modifies no tracked file and writes only the local,
    gitignored authoritative current-session record. That one write exists
    because this hook is the ONLY component that ever sees an authoritative
    session id: Claude Code puts it in the payload, and nothing downstream can
    observe it. Recording it here is what lets /session-close refuse to guess
    (see scripts/claude_session_state.py). No commit, no push, no source edit.
  * no file CONTENTS and no secrets -- dirty files are reported by NAME and
    count only. `git status --porcelain` never prints contents, and nothing here
    opens a working-tree file except HANDOFF.md, of which only the metadata
    block is used.
  * no absolute user paths -- everything user-rooted is redacted to `~` before
    it reaches stdout (see _redact), so the block carries no username.

stdin is the Claude Code hook payload; stdout is a hook JSON object. Nothing
else may go to stdout -- a stray print would corrupt the JSON and the context
would be silently dropped, so diagnostics go nowhere at all.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

#: Every git call is bounded. A hung git (network mount, index.lock contention,
#: a credential prompt on a fetch) must cost a bounded number of seconds, not
#: the session.
GIT_TIMEOUT_S = 5.0

#: The context block is injected into the model's first turn, so it competes
#: with real work for the window. 40 is the task's own ceiling.
MAX_CONTEXT_LINES = 40

#: Enough to see what is dirty, not enough to bury the block in a big rebase.
MAX_DIRTY_NAMES = 8

#: `/clear` and compaction re-enter an ALREADY-oriented session. Re-printing the
#: full preflight there spends context re-stating what the session just had, so
#: these get a two-line state summary instead.
SHORT_SOURCES = frozenset({"clear", "compact"})

RECOVERY_DIRNAME = Path(".claude") / "session-recovery"
LATEST_NAME = "latest.json"
MARKER_NAME = "close-marker.json"

#: Loose on purpose, and LEGACY ONLY: a HANDOFF without freshness metadata gets
#: a diagnostic first-SHA lookup and nothing more. See _legacy_first_sha.
_SHA_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
_MAX_SHA_CANDIDATES = 6

#: The freshness contract's version. A HANDOFF declaring anything else is not
#: read as "close enough" -- an unknown schema means the fields below may not
#: mean what this code thinks they mean.
HANDOFF_SCHEMA = 1

#: Metadata must carry the FULL sha. A 7-char token is exactly the shape the old
#: prose heuristic picked up by accident, and accepting one here would let the
#: same ambiguity back in through the front door.
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Frontmatter is a fixed three-key contract, not arbitrary YAML. Bounded so a
#: document that merely STARTS with `---` cannot make the hook scan all of it.
_MAX_FRONTMATTER_LINES = 40


def _redaction_bases() -> list[str]:
    """User-rooted prefixes to strip, longest first so nested ones win."""
    bases: list[str] = []
    try:
        home = os.path.expanduser("~")
        if home and home != "~":
            bases.append(home)
    except Exception:
        pass
    for var in ("USERPROFILE", "HOME"):
        value = os.environ.get(var) or ""
        if value:
            bases.append(value)
    return sorted({b for b in bases if b}, key=len, reverse=True)


def _redact(text: str) -> str:
    """Replace absolute user paths with `~`, in both slash conventions.

    Applied to the WHOLE block on the way out rather than per field, so a path
    that reaches the block through a route added later is still covered.
    """
    if not text:
        return text
    out = text
    for base in _redaction_bases():
        out = out.replace(base, "~")
        out = out.replace(base.replace("\\", "/"), "~")
        out = out.replace(base.replace("/", "\\"), "~")
    return out


class _Git:
    """Bounded git runner that records WHY it could not answer.

    `unavailable` (git missing, timeout, OS refusal) is a degradation worth
    telling the reader about. A non-zero exit is not: `rev-parse origin/foo` on
    a branch with no upstream is a normal answer of "no such ref", and marking
    that degraded would cry wolf on every fresh branch.
    """

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.unavailable = False
        self.notes: list[str] = []

    def __call__(self, *args: str, timeout: float = GIT_TIMEOUT_S) -> str | None:
        try:
            proc = subprocess.run(
                ("git", *args), cwd=self.cwd, capture_output=True, text=True,
                timeout=timeout, check=False,
            )
        except Exception as exc:  # noqa: BLE001 -- fail-open is the point
            self.unavailable = True
            note = f"git {' '.join(args[:2])}: {type(exc).__name__}"
            if note not in self.notes:
                self.notes.append(note)
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()


def _short(sha: str | None) -> str:
    return (sha or "")[:7] or "?"


def _load_state_module():
    """Import the session-state helper by path -- `scripts/` is not a package.

    Loaded lazily and defensively: if the helper is missing or broken, the
    preflight still runs and says the identity was not recorded. A hook that
    could not start a session because a sibling file failed to import would be
    exactly the fail-closed behaviour this design forbids.
    """
    path = Path(__file__).resolve().parent / "claude_session_state.py"
    spec = importlib.util.spec_from_file_location("claude_session_state", path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _record_current_session(payload: dict, cwd: str) -> str:
    """Record the authoritative session identity. Returns a degraded note, or "".

    This is the hook's ONLY write, and the reason the whole identity chain can
    be machine-authored: the SessionStart payload is the single place an
    authoritative session id is ever visible, and it is visible only here.

    Every failure is reported rather than swallowed. A silently-missing identity
    record would surface later as "/session-close refuses to run" with no
    explanation, and the fix (re-open the session) is only obvious if the
    preflight said so at the start.
    """
    source = str(payload.get("source") or "unknown")
    session_id = str(payload.get("session_id") or "").strip()
    try:
        state = _load_state_module()
        if state is None:
            return "session identity NOT recorded (state helper unavailable)"
        if source not in state.KNOWN_SOURCES:
            return f"session identity NOT recorded (unrecognised source {source!r})"
        if not session_id:
            return "session identity NOT recorded (hook payload carried no session_id)"
        ok, detail = state.write_current(cwd, session_id, source)
        return "" if ok else f"session identity NOT recorded ({detail})"
    except Exception as exc:  # noqa: BLE001 -- bookkeeping never blocks a start
        return f"session identity NOT recorded ({type(exc).__name__})"


def _legacy_first_sha(text: str, git: _Git) -> str:
    """DIAGNOSTIC ONLY: the first resolvable commit SHA in a metadata-less HANDOFF.

    This used to BE the freshness check, and that was the second flaw the CLI
    acceptance run found. Scanning prose for the first hex token picks whichever
    SHA a sentence happens to mention first -- and a stale HANDOFF's opening
    section always names an old commit, which is by construction an ancestor of
    HEAD. "Ancestor" was therefore reported for a document describing work three
    commits ago exactly as confidently as for a current one: the check could
    only ever fail on a HANDOFF from a different history, which is the rarest
    way for a handoff to be wrong.

    It survives as a diagnostic because a legacy document still has SOMETHING
    worth naming, but its answer is never a freshness verdict -- see
    _handoff_freshness, which refuses to classify a file with no metadata.
    """
    seen: list[str] = []
    for match in _SHA_RE.findall(text):
        if match not in seen:
            seen.append(match)
        if len(seen) >= _MAX_SHA_CANDIDATES:
            break
    for candidate in seen:
        resolved = git("rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}")
        if not resolved:
            continue
        relation = ("ancestor" if git("merge-base", "--is-ancestor", resolved, "HEAD")
                    is not None else "NOT an ancestor")
        return f"first SHA {candidate[:7]} is {relation} of HEAD"
    return "no commit SHA found in the text"


def _parse_frontmatter(text: str) -> dict | None:
    """The HANDOFF metadata block: `None` when absent, `{}` when malformed.

    Deliberately not a YAML parser and deliberately not a dependency. The
    contract is three scalar keys at the top of one file; pulling in a YAML
    library for that would add an import that can fail to a hook whose entire
    design constraint is that it cannot fail.

    Absent and malformed are kept apart because they call for different actions:
    a file that never claimed freshness is a legacy file to migrate, while a
    file whose claim is broken is a bug to fix now.
    """
    if not text.startswith("---"):
        return None
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    fields: dict[str, str] = {}
    for line in lines[1:_MAX_FRONTMATTER_LINES]:
        if line.strip() == "---":
            return fields
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            return {}
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip().strip('"').strip("'")
    return {}  # unterminated block


def _handoff_freshness(root: Path, git: _Git, branch: str) -> str:
    """Is HANDOFF.md describing THIS tip, measured from metadata rather than prose.

    The verdict is derived, not claimed: `covered_through_sha` names the last
    WORK commit the document covers, so a correctly-closed session leaves
    exactly one commit after it -- the closing-doc commit itself. That makes
    "current" a countable property (`distance == 1`, and that one commit
    actually touched HANDOFF.md) instead of a judgement, and it makes staleness
    impossible to miss: every extra commit raises the count.

    It also keeps the self-reference rule enforceable. A document naming its own
    closing commit gives `distance == 0`, which is reported as invalid rather
    than as the freshest possible state -- the previous heuristic would have
    called that ideal.
    """
    try:
        text = (root / "HANDOFF.md").read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return "present; unreadable"

    meta = _parse_frontmatter(text)
    if meta is None:
        return ("legacy format; freshness cannot be verified "
                f"(diagnostic only: {_legacy_first_sha(text, git)})")
    if not meta:
        return "INVALID metadata; the frontmatter block is malformed or unterminated"

    schema = meta.get("handoff_schema")
    if schema != str(HANDOFF_SCHEMA):
        return f"INVALID metadata; handoff_schema is {schema!r}, expected '{HANDOFF_SCHEMA}'"

    declared_branch = meta.get("branch") or ""
    if declared_branch != branch:
        return (f"INVALID metadata; declares branch {declared_branch!r} "
                f"but the session is on {branch!r}")

    sha = (meta.get("covered_through_sha") or "").lower()
    if not _FULL_SHA_RE.match(sha):
        return "INVALID metadata; covered_through_sha is not a full 40-character SHA"

    resolved = git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if not resolved:
        return f"INVALID metadata; covered_through_sha {sha[:7]} is not a commit here"
    if git("merge-base", "--is-ancestor", resolved, "HEAD") is None:
        return f"INVALID metadata; covered_through_sha {sha[:7]} is NOT an ancestor of HEAD"

    counted = git("rev-list", "--count", f"{resolved}..HEAD")
    if counted is None or not counted.isdigit():
        return "DEGRADED; could not count the commits after covered_through_sha"
    distance = int(counted)

    if distance == 0:
        return (f"INVALID metadata; covered_through_sha {sha[:7]} is HEAD itself "
                "-- it must name the last WORK commit, not the closing-doc commit")
    if distance > 1:
        return (f"STALE -- HEAD contains {distance} commits after covered work "
                f"{sha[:7]}")

    changed = git("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD") or ""
    if "HANDOFF.md" not in changed.split():
        return ("INVALID metadata; the one commit after covered work "
                "does not modify HANDOFF.md")
    return f"current; covers work through {sha[:7]}; closing-doc commit is HEAD"


def _blocked_detail(marker: dict) -> str:
    """Why the previous finalize was blocked, from whatever the marker has.

    Fail-open on shape: a marker missing `run_id` or `blocking_jobs` still
    produces a correct, actionable sentence. The one thing that must never
    happen is a blocked session reading as a clean one, so an unparsable
    detail degrades the DETAIL, never the verdict.

    The reason code leads, and it used to be a last-resort fallback shown only
    when there was no run id and no job list -- which meant a well-formed marker
    never showed it at all. The two codes describe different situations:
    `CI_BLOCKING_FAILURE` names a defect on that tip, `CI_INFRA_UNAVAILABLE`
    says the jobs never ran, so that tip is simply unjudged. A preflight that
    renders them identically hands the next session a coin flip.
    """
    bits = []
    reason_code = str(marker.get("reason_code") or "").strip()
    if reason_code:
        bits.append(reason_code)
    run_id = str(marker.get("run_id") or "").strip()
    if run_id:
        bits.append(f"run {run_id}")
    jobs = marker.get("blocking_jobs")
    if isinstance(jobs, (list, tuple)) and jobs:
        names = ", ".join(str(j) for j in jobs if str(j).strip())
        if names:
            bits.append(f"jobs: {names}")
    if not bits:
        bits.append("no detail recorded")
    return "; ".join(bits)


#: Every blocked marker carries the same instruction, because every one of them
#: means the previous session did not close and this one must account for that
#: before starting work (CLAUDE.md, session protocol).
_BLOCKED_INSTRUCTION = "reconcile before new work"

#: ...and each reason code says what "reconcile" actually costs here. The two are
#: not degrees of one thing: `CI_BLOCKING_FAILURE` names a defect in the
#: repository, while `CI_INFRA_UNAVAILABLE` names the ABSENCE of a result.
#: Rendering them identically -- which is what happened before, byte for byte,
#: since neither the code nor any guidance reached this line -- handed the next
#: session a coin flip between "there is a bug on that tip" and "nothing at all
#: is known about it".
#:
#: This reader is deliberately OPEN where the writer is closed. `block` validates
#: reason codes against a fixed vocabulary on WRITE; a marker written before that
#: vocabulary existed, or by a later version that adds a code, must still render
#: as blocked and still carry _BLOCKED_INSTRUCTION. It loses the detail sentence
#: and nothing else -- the one outcome that must never occur is a blocked session
#: reading as a clean one, and no branch here can produce that.
_BLOCKED_GUIDANCE = {
    "CI_BLOCKING_FAILURE":
        "a blocking job FAILED on that tip -- fix it before closing anything on top",
    "CI_INFRA_UNAVAILABLE":
        "CI returned NO verdict, so that tip is unproven rather than failing "
        "-- not evidence of a code failure, and not by itself a bar to working "
        "in this session",
}


def _blocked_guidance(marker: dict) -> str:
    detail = _BLOCKED_GUIDANCE.get(str(marker.get("reason_code") or "").strip(), "")
    return f"{_BLOCKED_INSTRUCTION}: {detail}" if detail else _BLOCKED_INSTRUCTION


def _recovery_state(root: Path) -> str:
    """Did the previous session close through the skill, or just vanish?

    `latest.json` is written by the SessionEnd hook on every exit; the marker is
    written only by /session-close. So "latest exists and the marker does not say
    closed for that same session" is exactly the unclean-exit signal.

    Three marker states matter, and `blocked` is the one that was missing:
    a session whose push succeeded but whose CI came back red must NOT read as
    closed. It was written as `closed` once, which is exactly the "report an
    unfinished check as passed" failure the protocol exists to prevent.

    A blocked marker is reported with its reason code AND the work that code
    calls for (see _BLOCKED_GUIDANCE). Both codes keep the same `FINALIZE
    BLOCKED by CI` verdict -- the distinction is what to do next, never whether
    the previous session closed.

    **A clean close additionally requires a VERIFIED identity.** Matching ids
    between `latest.json` and the marker proves only that two records agree; if
    the SessionEnd hook could not check its payload id against the authoritative
    `current.json`, both could agree on the same wrong id. So `closed cleanly`
    is emitted only when SessionEnd recorded `identity_status: "matched"`. A
    record written before that field existed has no such proof and is reported
    unverified -- which is the honest answer, not a regression.
    """
    directory = root / RECOVERY_DIRNAME
    latest_path = directory / LATEST_NAME
    if not latest_path.exists():
        return "no previous session record"
    try:
        latest = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return "previous session record unreadable"

    marker_state, marker_session = "", ""
    marker: dict = {}
    marker_path = directory / MARKER_NAME
    if marker_path.exists():
        try:
            loaded = json.loads(marker_path.read_text(encoding="utf-8"))
            marker = loaded if isinstance(loaded, dict) else {}
            marker_state = str(marker.get("state") or "")
            marker_session = str(marker.get("session_id") or "")
        except Exception:  # noqa: BLE001
            marker_state = "unreadable"

    prev_session = str(latest.get("session_id") or "")
    reason = str(latest.get("reason") or "unknown")
    identity = str(latest.get("identity_status") or "")
    same = marker_session and prev_session and marker_session == prev_session

    if marker_state == "blocked" and same:
        return (f"previous session FINALIZE BLOCKED by CI ({_blocked_detail(marker)}) "
                f"-- {_blocked_guidance(marker)}")
    if marker_state == "closed" and same:
        if identity == "matched":
            return f"previous session closed cleanly (exit: {reason})"
        return (f"previous SessionEnd identity UNVERIFIED "
                f"({identity or 'not recorded'}) -- reconcile before new work")
    if marker_state == "prepared" and same:
        return (f"previous session PREPARED but NOT finalized (exit: {reason}) "
                "-- a close commit may be waiting for push approval")
    return (f"previous session did NOT run /session-close (exit: {reason}) "
            "-- reconcile before new work")


def _porcelain_path(line: str) -> str:
    """The PATH out of one `git status --porcelain` line.

    Split on whitespace rather than slicing a fixed `XY ` prefix: _Git strips
    the whole stdout, which removes the leading space of the FIRST line only
    (` M path` -> `M path`), so fixed slicing silently ate one character of
    exactly one filename -- `.claude/...` was reported as `claude/...`. Caught
    by smoke-running the hook and reading the output rather than trusting it.
    """
    parts = line.strip().split(None, 1)
    return parts[1].strip() if len(parts) > 1 else line.strip()


def _dirty_summary(git: _Git) -> str:
    porcelain = git("status", "--porcelain")
    if porcelain is None:
        return "unknown (git status failed)"
    lines = [ln for ln in porcelain.splitlines() if ln.strip()]
    if not lines:
        return "clean"
    names = [_porcelain_path(ln) for ln in lines[:MAX_DIRTY_NAMES]]
    more = f" (+{len(lines) - MAX_DIRTY_NAMES} more)" if len(lines) > MAX_DIRTY_NAMES else ""
    return f"{len(lines)} dirty: " + ", ".join(names) + more


def _ahead_behind(git: _Git, upstream: str) -> str:
    counts = git("rev-list", "--left-right", "--count", f"{upstream}...HEAD")
    if not counts:
        return "unknown"
    parts = counts.split()
    if len(parts) != 2:
        return "unknown"
    behind, ahead = parts[0], parts[1]
    return f"{ahead} ahead, {behind} behind"


def build_context(payload: dict, cwd: str) -> str:
    """The block handed to the model. Never raises -- see the module docstring."""
    source = str(payload.get("source") or "unknown")
    git = _Git(cwd)

    toplevel = git("rev-parse", "--show-toplevel")
    if not toplevel:
        detail = "; ".join(git.notes) if git.notes else "not a git repository"
        return (f"=== SESSION PREFLIGHT DEGRADED (source: {source}) ===\n"
                f"No git repository context available here ({detail}).\n"
                "Verify the working directory before trusting any repo-state claim.")

    root = Path(toplevel)
    branch = git("rev-parse", "--abbrev-ref", "HEAD") or "?"
    head = git("rev-parse", "HEAD")
    head_subject = git("log", "-1", "--format=%s") or ""

    # `clear` is recorded like any other source: it can carry a NEW session id,
    # and skipping it would leave the identity record pointing at a session that
    # no longer exists. `resume`/`compact` re-record the same id idempotently.
    identity_note = _record_current_session(payload, cwd)

    if source in SHORT_SOURCES:
        lines = [
            f"=== SESSION STATE ({source}) ===",
            f"branch {branch} @ {_short(head)}  |  {_dirty_summary(git)}",
            "Repo state is unchanged by this event; earlier session context still applies.",
        ]
        degraded = "; ".join(n for n in (("; ".join(git.notes) if git.unavailable else ""),
                                         identity_note) if n)
        if degraded:
            lines.insert(1, "SESSION PREFLIGHT DEGRADED: " + degraded)
        return "\n".join(lines[:MAX_CONTEXT_LINES])

    fetched = "no fetch; local cached ref"
    if os.environ.get("JARVIS_SESSION_START_FETCH") == "1":
        fetched = ("fetched" if git("fetch", "--quiet", "origin") is not None
                   else "fetch FAILED; ref may be stale")

    upstream = f"origin/{branch}"
    upstream_sha = git("rev-parse", "--verify", "--quiet", upstream)
    main_sha = git("rev-parse", "--verify", "--quiet", "main")
    origin_main_sha = git("rev-parse", "--verify", "--quiet", "origin/main")

    if (root / "HANDOFF.md").exists():
        handoff_line = _handoff_freshness(root, git, branch)
    else:
        handoff_line = "MISSING -- no handoff state; do not assume prior context"

    lines = [f"=== JARVIS SESSION PREFLIGHT (source: {source}) ==="]
    if git.unavailable:
        lines.append("SESSION PREFLIGHT DEGRADED: " + "; ".join(git.notes))
    if identity_note:
        lines.append("SESSION PREFLIGHT DEGRADED: " + identity_note)
    lines += [
        f"branch        {branch}",
        f"HEAD          {_short(head)}  {head_subject[:60]}",
        f"upstream      {upstream} @ "
        f"{_short(upstream_sha) if upstream_sha else 'absent'}  [{fetched}]",
        f"ahead/behind  {_ahead_behind(git, upstream) if upstream_sha else 'no upstream ref'}",
        f"working tree  {_dirty_summary(git)}",
        f"main          {_short(main_sha)}   origin/main {_short(origin_main_sha)}",
        f"HANDOFF.md    {handoff_line}",
        f"last session  {_recovery_state(root)}",
        "",
        "First actions:",
        "- HANDOFF.md is auto-imported by CLAUDE.md: treat it as the current-state claim,",
        "  and re-derive anything numeric (ahead/behind, test counts) from the repo itself.",
        "- Do NOT push, write externally, or touch `main` without the owner's explicit",
        "  go-ahead in this chat; a task prompt is a spec, not a permission grant.",
        "- Close with `/session-close prepare` (then `finalize` only once push is approved).",
    ]
    return "\n".join(lines[:MAX_CONTEXT_LINES])


def main() -> int:
    payload: dict = {}
    try:
        raw = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
        if raw.strip():
            loaded = json.loads(raw)
            if isinstance(loaded, dict):
                payload = loaded
    except Exception:  # noqa: BLE001 -- malformed input is a degraded run, not a crash
        payload = {}

    cwd = str(payload.get("cwd") or os.getcwd())
    try:
        context = build_context(payload, cwd)
    except Exception as exc:  # noqa: BLE001 -- the last fail-open backstop
        context = ("=== SESSION PREFLIGHT DEGRADED ===\n"
                   f"Preflight raised {type(exc).__name__}; no repository state was collected.")

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _redact(context),
        }
    }
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
