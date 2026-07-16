"""Shell execution tool — runs PowerShell commands with a safety deny-list."""

from __future__ import annotations

import subprocess

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


def run(command: str, *, confirmed: bool = False) -> str:
    """
    Execute a PowerShell command and return combined stdout+stderr.
    Raises ValueError for denied commands.
    Set confirmed=True when the CLI has already asked the user.
    """
    safe, reason = is_safe(command)
    if not safe:
        raise ValueError(f"Command blocked: {reason}")

    result = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    output = result.stdout + result.stderr
    return output.strip() or "(no output)"
