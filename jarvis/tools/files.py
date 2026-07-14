"""File operation tools — scoped to workspace or user home directory."""

from __future__ import annotations

import os
from pathlib import Path

# Files in these directories can never be read/written by the agent
PROTECTED_DIRS = {".venv", ".git", "data"}
MAX_READ_BYTES = 512_000  # 512 KB

# Faz 2 — meta memory: persona/safety/directive files stay agent-*readable*
# (the agent needs to see its own instructions) but must never be agent-
# *writable* — a self-editing safety directive is actively unsafe (see
# MEMORY.md's local-first plan, "de-prioritized" list). Checked only in
# write(), not read() or the shared _resolve().
PROTECTED_WRITE_PREFIXES = (Path("jarvis") / "prompts" / "core",)

_HOME = Path(os.path.expanduser("~")).resolve()


def _is_within(child: Path, parent: Path) -> bool:
    return parent == child or parent in child.parents


def _resolve(path_str: str, workspace: Path) -> Path:
    """Resolve a relative or absolute path.

    Allows paths inside workspace (relative or absolute) and absolute paths
    inside the user's home directory (Desktop, Documents, OneDrive, etc.).
    """
    raw = Path(path_str)
    p = raw.resolve() if raw.is_absolute() else (workspace / raw).resolve()

    ws_root = workspace.resolve()
    if not (_is_within(p, ws_root) or _is_within(p, _HOME)):
        raise PermissionError(
            f"Path '{path_str}' is outside the workspace and home directory."
        )
    for part in p.parts:
        if part in PROTECTED_DIRS:
            raise PermissionError(f"Path '{path_str}' is inside a protected directory.")
    return p


def read(path_str: str, workspace: Path) -> str:
    """Read a text file and return its contents (up to 512 KB)."""
    p = _resolve(path_str, workspace)
    if not p.exists():
        raise FileNotFoundError(f"File not found: {path_str}")
    raw = p.read_bytes()
    if len(raw) > MAX_READ_BYTES:
        raw = raw[:MAX_READ_BYTES]
        return raw.decode("utf-8", errors="replace") + "\n\n[... file truncated at 512 KB ...]"
    return raw.decode("utf-8", errors="replace")


def write(path_str: str, content: str, workspace: Path) -> str:
    """Write content to a file, creating parent directories if needed."""
    p = _resolve(path_str, workspace)
    for prefix in PROTECTED_WRITE_PREFIXES:
        if _is_within(p, (workspace / prefix).resolve()):
            raise PermissionError(
                f"Path '{path_str}' is inside a protected persona/directive directory "
                f"and can never be written by the agent — meta-memory is human-edited only."
            )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"Written {len(content)} chars to {p.relative_to(workspace)}"


def list_dir(path_str: str, workspace: Path) -> str:
    """List files and directories at path."""
    p = _resolve(path_str, workspace)
    if not p.exists():
        raise FileNotFoundError(f"Directory not found: {path_str}")
    if not p.is_dir():
        raise NotADirectoryError(f"Not a directory: {path_str}")
    entries = sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name))
    lines = []
    for e in entries:
        if e.name.startswith("."):
            continue
        prefix = "📁 " if e.is_dir() else "📄 "
        lines.append(f"{prefix}{e.name}")
    return "\n".join(lines) if lines else "(empty directory)"
