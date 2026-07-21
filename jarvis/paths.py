"""Central filesystem-path resolution — the JARVIS_HOME isolation root.

Stabilization sprint (2026-07-15): every runtime storage location (SQLite
stores, usage file, audit log, kill switch, ChromaDB, vault, uploads, tool
caches/outputs, OAuth token files) derives its base from this module instead
of hardcoding ``Path("data")/...`` literals scattered across the codebase.

Behavior:

- ``JARVIS_HOME`` unset/empty  -> ``jarvis_home()`` is ``Path(".")`` — identical
  to the historical cwd-relative behavior, so normal runs and the existing
  ``isolated_cwd`` test fixture keep working unchanged.
- ``JARVIS_HOME`` set          -> every store resolves under that root,
  regardless of cwd AND regardless of the repo location (this also covers the
  project-root-anchored Google OAuth tokens that a chdir-based isolation
  would silently miss — see ``project_data_dir``).

The env var is read on every call (no module-level cache) so tests can
``monkeypatch.setenv`` without fighting stale state — the ``kill_switch._cache``
lesson from MEMORY.md applies here in reverse.
"""
from __future__ import annotations

import os
from pathlib import Path

# jarvis/paths.py -> jarvis/ -> repo root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def jarvis_home() -> Path:
    """Isolation root: ``JARVIS_HOME`` env var if set, else cwd (historical)."""
    env = os.environ.get("JARVIS_HOME", "").strip()
    return Path(env) if env else Path(".")


def data_dir() -> Path:
    """Root for all runtime storage (sessions.db, usage.json, caches, ...)."""
    return jarvis_home() / "data"


def resolve(p: Path | str) -> Path:
    """Resolve a (possibly relative) configured path against ``jarvis_home()``.

    Absolute paths pass through untouched; relative Settings values like
    ``vault_dir=Path("vault")`` land under the isolation root when JARVIS_HOME
    is set and stay cwd-relative (today's behavior) when it isn't.
    """
    p = Path(p)
    return p if p.is_absolute() else jarvis_home() / p


def resolve_project(p: Path | str) -> Path:
    """Like ``resolve()``, but the no-JARVIS_HOME anchor is the repo root, not cwd.

    For files that must be found regardless of launch directory (the Google
    OAuth client-secret referenced by ``settings.google_calendar_creds_file``).
    JARVIS_HOME, when set, still wins — isolation overrides convenience.
    """
    p = Path(p)
    if p.is_absolute():
        return p
    env = os.environ.get("JARVIS_HOME", "").strip()
    if env:
        return Path(env) / p
    return _PROJECT_ROOT / p


def cache_dir() -> Path:
    """Root for large, machine-level model/voice caches (Silero VAD weights,
    Piper voices) -- Agent Runtime rev.2, Faz 5 (closes the two "Path.home()
    bypass" findings in jarvis/voice/vad.py and jarvis/voice/tts_piper.py).

    Deliberately NOT the same shape as data_dir(): those are large downloads
    a developer expects to survive switching launch directories, not
    per-run/per-workspace data, so the no-JARVIS_HOME default is the real OS
    home (``~/.cache/jarvis``, downloaded once, reused regardless of cwd) --
    matching jarvis.tools.files._effective_home's same real-home-in-
    production / JARVIS_HOME-redirected-in-tests split, for the same reason.
    JARVIS_HOME, when set (test isolation, the eval harness), still wins: an
    isolated run must never read or write the developer's real cache, and
    must never silently download a multi-MB model into it either. Read per
    call, not cached at import -- same reason as jarvis_home() above.
    """
    env = os.environ.get("JARVIS_HOME", "").strip()
    if env:
        return Path(env) / ".cache" / "jarvis"
    return Path.home() / ".cache" / "jarvis"


def project_data_dir() -> Path:
    """data/ dir for files that must be found regardless of cwd (OAuth tokens).

    gmail/calendar/email_triage deliberately anchor their token files to the
    repo root (not cwd) so daily runs from any directory reuse one token.
    That stays the default — but JARVIS_HOME, when set, overrides even this,
    so an isolated test profile can never read or write the real tokens.
    """
    env = os.environ.get("JARVIS_HOME", "").strip()
    if env:
        return Path(env) / "data"
    return _PROJECT_ROOT / "data"
