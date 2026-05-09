"""Run a Python script with the current interpreter — for plot generation, etc."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TIMEOUT_SECONDS = 60
MAX_OUTPUT_CHARS = 4000


def run_script(script_path: Path, cwd: Path | None = None) -> str:
    """Execute a Python script and return its stdout + stderr."""
    if not script_path.exists():
        return f"[ERROR] Script not found: {script_path}"
    try:
        result = subprocess.run(
            [sys.executable, str(script_path)],
            cwd=str(cwd or script_path.parent),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"[ERROR] Script exceeded {TIMEOUT_SECONDS}s timeout."
    except Exception as exc:
        return f"[ERROR] Failed to run script: {exc}"

    output = (result.stdout + result.stderr).strip() or "(no output)"
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + "\n[... truncated ...]"
    status = "OK" if result.returncode == 0 else f"EXIT {result.returncode}"
    return f"[{status}]\n{output}"
