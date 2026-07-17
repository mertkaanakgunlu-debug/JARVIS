"""Shell execution tool — runs PowerShell commands with a safety deny-list."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

# Commands/patterns that are never allowed, no matter what
DENY_PATTERNS = [
    "rm ", "rm\t", "Remove-Item", "del ", "del\t",
    "rd ", "rmdir",
    "format ",
    "shutdown", "restart-computer",
    "reg ", "regedit",
    "net user", "net localgroup",
    "cipher /w",
    "diskpart",
    "bcdedit",
    "takeown",
    "icacls",
    "attrib -r -s -h",
    "sfc /scannow",        # not dangerous but unnecessary
    "> nul",               # output suppression tricks
    # GPT-5.6 review remediation, Faz 6 (verdict #5, CONFIRMED): these were
    # entirely absent -- a trivial deny-list bypass, since none of the
    # patterns above stop a command from just re-running itself through
    # PowerShell's own dynamic-execution/reflection primitives instead of
    # calling a denied cmdlet directly.
    "invoke-expression", "iex ", "iex(",
    "-encodedcommand", "-enc ",
    "[reflection.assembly]", "add-type",
    "downloadstring", "downloadfile", "net.webclient",
]

TIMEOUT_SECONDS = 30


def contains_denied_pattern(text: str) -> tuple[bool, str]:
    """Return (found, pattern) -- case-insensitive substring match against
    DENY_PATTERNS. Shared between this module's own is_safe() and
    jarvis/tools/python_exec.py's script-content pre-scan (Faz 6): a
    generated Python script that just shells out to the same dangerous
    PowerShell text must not be able to bypass this deny-list by going
    through python_run instead of shell_run."""
    lower = text.lower()
    for pattern in DENY_PATTERNS:
        if pattern.lower() in lower:
            return True, pattern
    return False, ""


def is_safe(command: str) -> tuple[bool, str]:
    """Return (is_safe, reason). Caller decides whether to proceed."""
    found, pattern = contains_denied_pattern(command)
    if found:
        return False, f"Command contains denied pattern: '{pattern}'"
    return True, ""


# cd-family verbs that move the working directory. run() already sets
# cwd=workspace, so relative work stays put; this catches the explicit-escape
# vector (a command that changes directory OUT of the workspace before doing
# its real work). Group 'target' is the first whitespace-delimited argument up
# to the next statement separator.
_CD_ESCAPE = re.compile(
    r"\b(?:cd|chdir|sl|Set-Location|Push-Location)\b\s+(?P<target>[^;|&\r\n]+)",
    re.IGNORECASE,
)


def escapes_workspace(command: str, workspace: str | Path) -> tuple[bool, str]:
    """Best-effort: does the command try to change directory OUT of the
    workspace?

    NOT a sandbox — a determined absolute-path *read* (``Get-Content C:\\...``)
    is still possible and remains covered only by shell_run's L3 confirmation
    gate. This closes the concrete leak the manual round found: a bare ``dir``
    listing the real repo root instead of the isolated test home. Kept
    conservative to avoid false-blocking legitimate in-workspace ``cd data``.
    """
    ws = Path(workspace).resolve()
    for m in _CD_ESCAPE.finditer(command or ""):
        raw = m.group("target").strip().strip('"').strip("'")
        if not raw:
            continue
        parts = re.split(r"[\\/]+", raw)
        if ".." in parts:  # parent traversal
            return True, f"directory change escapes workspace: '{raw}'"
        if raw[:1] in ("\\", "/"):  # current-drive root (\ or /)
            return True, f"directory change escapes workspace: '{raw}'"
        p = Path(raw)
        if p.is_absolute():
            try:
                p.resolve().relative_to(ws)
            except ValueError:
                return True, f"directory change escapes workspace: '{raw}'"
    return False, ""


def run(command: str, *, confirmed: bool = False, cwd: str | Path | None = None) -> str:
    """
    Execute a PowerShell command and return combined stdout+stderr.
    Raises ValueError for denied commands.
    Set confirmed=True when the CLI has already asked the user.
    cwd pins the working directory (the caller passes the tool workspace so a
    bare ``dir``/``ls`` lists the isolated home, not the process's real cwd).
    """
    safe, reason = is_safe(command)
    if not safe:
        raise ValueError(f"Command blocked: {reason}")

    result = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        cwd=str(cwd) if cwd else None,
    )
    output = result.stdout + result.stderr
    return output.strip() or "(no output)"
