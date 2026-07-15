"""jarvis/tools/files.py -- workspace/home-scoped file read/write.

Covers BUG-20 (file_write raised an uncaught ValueError for any path under
the home directory but outside workspace, since the return message did
`p.relative_to(workspace)` unconditionally) plus the pre-existing protected-
path guards it must not have regressed.
"""
from __future__ import annotations

import pytest

from jarvis.tools import files


def test_write_and_read_within_workspace(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    msg = files.write("notes.txt", "hello", ws)
    assert "notes.txt" in msg
    assert files.read("notes.txt", ws) == "hello"


def test_write_under_home_but_outside_workspace_does_not_raise(tmp_path, monkeypatch):
    """BUG-20 regression: _resolve() already allowed this path (it's under
    the home directory), but write()'s own return-message formatting used to
    crash on it regardless."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    # _HOME is always fully resolved in production (Path(...).resolve() at
    # import time) -- match that here so _is_within()'s containment check
    # can't spuriously fail on a resolution mismatch.
    fake_home = (tmp_path / "home").resolve()
    (fake_home / "Desktop").mkdir(parents=True)
    monkeypatch.setattr(files, "_HOME", fake_home)

    target = str(fake_home / "Desktop" / "outside_workspace.txt")
    msg = files.write(target, "hello", ws)  # must not raise ValueError
    assert "outside_workspace.txt" in msg
    assert (fake_home / "Desktop" / "outside_workspace.txt").read_text(encoding="utf-8") == "hello"


def test_write_outside_workspace_and_home_is_rejected(tmp_path, monkeypatch):
    ws = tmp_path / "workspace"
    ws.mkdir()
    fake_home = (tmp_path / "home").resolve()
    fake_home.mkdir()
    monkeypatch.setattr(files, "_HOME", fake_home)

    outside = tmp_path / "elsewhere" / "file.txt"
    with pytest.raises(PermissionError):
        files.write(str(outside), "x", ws)


def test_write_rejects_protected_dir_component(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    with pytest.raises(PermissionError):
        files.write(".git/hooks/evil", "x", ws)


def test_write_rejects_protected_persona_prefix(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "jarvis" / "prompts" / "core").mkdir(parents=True)
    with pytest.raises(PermissionError):
        files.write("jarvis/prompts/core/01_persona.md", "evil override", ws)


def test_read_still_works_under_protected_persona_prefix(tmp_path):
    """Meta-memory files stay agent-readable, only not agent-writable."""
    ws = tmp_path / "workspace"
    core = ws / "jarvis" / "prompts" / "core"
    core.mkdir(parents=True)
    (core / "01_persona.md").write_text("persona text", encoding="utf-8")
    assert files.read("jarvis/prompts/core/01_persona.md", ws) == "persona text"


def test_read_missing_file_raises_file_not_found(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    with pytest.raises(FileNotFoundError):
        files.read("does_not_exist.txt", ws)
