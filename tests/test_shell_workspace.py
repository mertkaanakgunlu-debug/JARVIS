"""Faz 1.2 — shell_run must operate inside the tool workspace, not the
process cwd.

The manual round found a bare ``dir`` listing the real repo root (``.venv``,
``CLAUDE.md``) instead of the isolated ``JARVIS_TEST_HOME`` — shell_run, unlike
file_read/write/list, never received the workspace. Fixed by threading
``cwd=workspace`` into ``shell.run`` plus a best-effort cd/Set-Location escape
guard (not a sandbox; absolute-path reads stay covered by the L3 gate).
"""
from __future__ import annotations

from jarvis.config import Settings
from jarvis.graph import tools as graph_tools
from jarvis.memory import Memory
from jarvis.tools import shell


# ── escapes_workspace: pure heuristic ────────────────────────────────────────

def test_escape_guard_allows_in_workspace(tmp_path):
    assert shell.escapes_workspace("dir", tmp_path) == (False, "")
    assert shell.escapes_workspace("cd data; dir", tmp_path)[0] is False
    assert shell.escapes_workspace("Get-ChildItem .\\sub", tmp_path)[0] is False


def test_escape_guard_blocks_parent_and_absolute(tmp_path):
    assert shell.escapes_workspace("cd ..", tmp_path)[0] is True
    assert shell.escapes_workspace("cd ..\\..\\Windows", tmp_path)[0] is True
    assert shell.escapes_workspace("Set-Location C:\\Windows", tmp_path)[0] is True
    assert shell.escapes_workspace("cd \\", tmp_path)[0] is True


def test_escape_guard_absolute_inside_workspace_allowed(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    assert shell.escapes_workspace(f"cd {sub}", tmp_path)[0] is False


# ── run() honours cwd (real PowerShell; CI is windows-latest) ────────────────

def test_run_honors_cwd(tmp_path):
    out = shell.run("(Get-Location).Path", cwd=tmp_path)
    assert tmp_path.name in out


# ── shell_run wrapper wiring ─────────────────────────────────────────────────

def _shell_run_tool(tmp_path):
    settings = Settings()
    memory = Memory(settings)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = next(t for t in graph_tools.make_tools(workspace, settings, memory)
                if t.name == "shell_run")
    return tool, workspace


def test_wrapper_passes_workspace_cwd(monkeypatch, isolated_cwd, tmp_path):
    captured: dict = {}
    # graph_tools.shell_tools IS the jarvis.tools.shell module — one patch covers
    # the closure's shell_tools.run lookup.
    monkeypatch.setattr(shell, "run", lambda command, **kw: captured.update(kw) or "(no output)")
    tool, workspace = _shell_run_tool(tmp_path)

    tool.invoke({"command": "dir"})
    assert captured.get("cwd") == workspace


def test_wrapper_blocks_escape_without_running(monkeypatch, isolated_cwd, tmp_path):
    ran = {"v": False}
    monkeypatch.setattr(shell, "run", lambda *a, **k: ran.__setitem__("v", True) or "x")
    tool, _ = _shell_run_tool(tmp_path)

    out = tool.invoke({"command": "cd ..; dir"})
    assert out.startswith("[BLOCKED]")
    assert ran["v"] is False
