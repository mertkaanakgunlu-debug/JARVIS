"""Shared pytest fixtures for the JARVIS test suite (Faz 8).

No test suite existed anywhere in this repo before Faz 8 (see CLAUDE.md /
CONTRIBUTING.md). This file exists mainly to enforce one rule everywhere:
never let a test touch the real project's data/ directory.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make `import jarvis...` work regardless of where pytest is invoked from.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def isolated_cwd(tmp_path, monkeypatch):
    """Chdir into a fresh temp dir for the test's duration.

    MANDATORY before constructing/calling anything that resolves storage paths
    as Path("data")/... relative to cwd -- JarvisAgent, SessionStore, Memory,
    TodoStore, SchedulerStore, UsageTracker, kill_switch, audit_log, the
    jarvis/api.py upload dir, etc. See MEMORY.md's isolate-test-data-paths
    lesson: a prior session's throwaway verification script chdir'd to the
    REAL project root to satisfy one of these relative paths and silently
    overwrote the user's actual latest conversation history. tmp_path is a
    fresh directory per test (pytest builtin); monkeypatch.chdir restores the
    original cwd on teardown even if the test raises.

    Also resets jarvis.kill_switch's module-level _cache: that module reads a
    cwd-relative file but caches the last successful read at module level
    (across whichever cwd was active at the time) -- without a reset here, a
    fresh isolated tmp_path with no data/kill_switch.json yet would silently
    inherit a *different* test's cached state instead of the true on-disk-
    absent default. See jarvis/kill_switch.py's _load() docstring for why the
    cache exists at all (last-resort fallback for a transient read failure).
    """
    monkeypatch.chdir(tmp_path)
    import jarvis.kill_switch as kill_switch
    monkeypatch.setattr(kill_switch, "_cache", None)
    return tmp_path
