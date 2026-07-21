"""Agent Runtime rev.2, Faz 5 -- permanent guard against host-path leaks.

AST-scans every jarvis/**/*.py file for direct use of the primitives that
resolve to the REAL developer machine's filesystem, bypassing JARVIS_HOME
isolation entirely: os.path.expanduser("~") / Path.home() /
os.environ["USERPROFILE"] / os.getcwd(). Two files are the deliberate,
whitelisted exception -- both already implement a correct "JARVIS_HOME
redirect first, real path only as the production fallback" helper
(jarvis.tools.files._effective_home, jarvis.paths.cache_dir) that the rest
of the codebase is expected to call INSTEAD of touching these primitives
directly.

This is not a one-time sweep: jarvis/voice/vad.py and jarvis/voice/tts_piper.py
had exactly this bug (a module-level Path.home()-based constant, frozen at
import time, silently downloading/reading model caches from the real
machine even under an isolated JARVIS_HOME test/eval profile) until this
phase. This test exists so a third occurrence fails CI immediately instead
of waiting for someone to notice a test run touched the real ~/.cache.
"""
from __future__ import annotations

import ast
from pathlib import Path

_JARVIS_ROOT = Path(__file__).resolve().parent.parent / "jarvis"

# Relative to jarvis/. Both already implement the correct JARVIS_HOME-first /
# real-path-fallback pattern -- see their own docstrings
# (jarvis.tools.files._effective_home, jarvis.paths.cache_dir).
_WHITELISTED_FILES = {
    Path("tools") / "files.py",
    Path("paths.py"),
}

_FORBIDDEN_ATTRS = {"expanduser", "home", "getcwd"}
_FORBIDDEN_NAMES = {"expanduser", "getcwd"}  # covers `from os.path import expanduser` style


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return [(lineno, description), ...] for every forbidden usage found."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _FORBIDDEN_ATTRS:
                hits.append((node.lineno, f"{func.attr}() call"))
            elif isinstance(func, ast.Name) and func.id in _FORBIDDEN_NAMES:
                hits.append((node.lineno, f"{func.id}() call"))
        elif isinstance(node, ast.Constant) and node.value == "USERPROFILE":
            hits.append((node.lineno, "'USERPROFILE' literal"))
    return hits


def _all_jarvis_files() -> list[Path]:
    return sorted(_JARVIS_ROOT.rglob("*.py"))


def test_no_host_path_leak_outside_the_whitelisted_helpers():
    violations: list[str] = []
    for path in _all_jarvis_files():
        rel = path.relative_to(_JARVIS_ROOT)
        if rel in _WHITELISTED_FILES:
            continue
        for lineno, desc in _scan_file(path):
            violations.append(f"jarvis/{rel.as_posix()}:{lineno} -- {desc}")
    assert not violations, (
        "Found host-filesystem primitive(s) outside the whitelisted isolation "
        "helpers (jarvis/tools/files.py, jarvis/paths.py) -- these bypass "
        "JARVIS_HOME isolation for tests/eval runs. Route through jarvis.paths "
        "(or jarvis.tools.files._effective_home for the workspace-escape "
        "security boundary) instead:\n" + "\n".join(violations)
    )


def test_the_whitelisted_files_still_exist():
    """Guards the guard: a rename/deletion of either whitelisted file must
    not silently make the allowlist vacuous."""
    for rel in _WHITELISTED_FILES:
        assert (_JARVIS_ROOT / rel).exists(), f"jarvis/{rel.as_posix()} whitelisted but missing"


def test_the_whitelisted_files_do_contain_a_real_usage():
    """If neither whitelisted file actually uses one of these primitives
    anymore, the allowlist is dead weight -- keeps it honest rather than a
    permanent blank check nobody revisits."""
    found_any = any(_scan_file(_JARVIS_ROOT / rel) for rel in _WHITELISTED_FILES)
    assert found_any, "neither whitelisted file uses a host-path primitive -- shrink the allowlist"
