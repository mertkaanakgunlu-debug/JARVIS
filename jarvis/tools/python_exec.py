"""Run a Python script with the current interpreter — for plot generation, etc."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from jarvis.tools.shell import contains_denied_pattern

TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARS = 4000


def run_script(script_path: Path, cwd: Path | None = None, timeout: float = TIMEOUT_SECONDS) -> str:
    """Execute a Python script and return its stdout + stderr.

    GPT-5.6 review remediation, Faz 6 (verdict #6, "worse than shell_run" --
    this had zero content checks at all, a complete escape hatch around
    shell_run's own deny-list: a generated "plot script" could just
    os.system()/subprocess a dangerous PowerShell command shell_run would
    refuse to run directly). This is a basic guard, NOT a sandbox -- the
    subprocess still has no resource/network restrictions; see docs/SAFETY.md
    "Known limits". Reuses shell.py's deny-list against the script's raw
    source text before ever executing it.

    Agent Runtime rev.2, Faz 3: timeout now defaults to this module's own
    constant (unchanged for any caller that doesn't pass one) but
    jarvis/graph/tools.py's python_run wrapper passes ToolSpec.timeout_seconds
    explicitly. subprocess.TimeoutExpired is deliberately NOT caught here
    anymore (pre-Faz-3 this returned a bare "[ERROR] ... timeout" string that
    bypassed the shared category=timeout/retryable=true/worker_terminated
    reporting entirely) -- it propagates to safe_tools.py's shared exception
    boundary, same as shell.py's run().
    """
    if not script_path.exists():
        return f"[ERROR] Script not found: {script_path}"
    try:
        source = script_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"[ERROR] Could not read script: {exc}"
    found, pattern = contains_denied_pattern(source)
    if found:
        return f"[ERROR] Script blocked: contains denied pattern '{pattern}'"
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(cwd or script_path.parent),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise
    except Exception as exc:
        return f"[ERROR] Failed to run script: {exc}"

    output = (result.stdout + result.stderr).strip() or "(no output)"
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n[... truncated ...]"
    status = "OK" if result.returncode == 0 else f"EXIT {result.returncode}"
    return f"[{status}]\n{output}"
