"""Shell execution tool — runs PowerShell commands with a safety deny-list."""

from __future__ import annotations

import subprocess
import shlex

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
]

TIMEOUT_SECONDS = 30


def is_safe(command: str) -> tuple[bool, str]:
    """Return (is_safe, reason). Caller decides whether to proceed."""
    lower = command.lower()
    for pattern in DENY_PATTERNS:
        if pattern.lower() in lower:
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
