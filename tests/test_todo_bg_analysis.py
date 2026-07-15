"""jarvis/graph/tools.py -- BUG-16 regression.

todo("add")'s background prioritization used asyncio.create_task() with no
reference kept anywhere. asyncio only holds a *weak* reference to a task --
one with no other referent is eligible for garbage collection before it
finishes, silently killing the analysis before it ever calls store.update().
Fixed with a module-level strong-reference set (_todo_bg_tasks), pruned via
a done-callback once each task actually completes.

analyze_and_save (the real Gemini call) is mocked -- this is about the task
surviving to completion, not analysis quality.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from jarvis.config import Settings
from jarvis.graph import tools as graph_tools
from jarvis.memory import Memory
from jarvis.todo_store import TodoStore


async def test_todo_add_background_analysis_runs_to_completion(monkeypatch, isolated_cwd, tmp_path):
    calls = []

    async def fake_analyze_and_save(todo_id, title, description, settings, store):
        calls.append(todo_id)
        store.update(todo_id, priority="urgent_important", priority_score=0.9, instructions="1. Do it")

    import jarvis.todo_analyzer as todo_analyzer
    monkeypatch.setattr(todo_analyzer, "analyze_and_save", fake_analyze_and_save)

    settings = Settings()
    memory = Memory(settings)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    todo_tool = next(t for t in graph_tools.make_tools(workspace, settings, memory) if t.name == "todo")

    result = await todo_tool.ainvoke({"action": "add", "title": "Test task"})
    assert "eklendi" in result

    # Background task was scheduled via create_task(), not awaited directly --
    # give it a chance to actually run.
    for _ in range(50):
        if calls:
            break
        await asyncio.sleep(0.02)

    assert calls, "background analysis never ran to completion (task was lost)"

    store = TodoStore(Path("data/sessions.db"))
    saved = store.get(calls[0])
    assert saved["priority"] == "urgent_important"
    assert saved["instructions"] == "1. Do it"


async def test_bg_task_is_tracked_then_pruned_after_completion(monkeypatch, isolated_cwd, tmp_path):
    """Directly exercises the _todo_bg_tasks registry the fix introduced."""
    release = asyncio.Event()

    async def fake_analyze_and_save(todo_id, title, description, settings, store):
        await release.wait()

    import jarvis.todo_analyzer as todo_analyzer
    monkeypatch.setattr(todo_analyzer, "analyze_and_save", fake_analyze_and_save)

    settings = Settings()
    memory = Memory(settings)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    todo_tool = next(t for t in graph_tools.make_tools(workspace, settings, memory) if t.name == "todo")

    assert len(graph_tools._todo_bg_tasks) == 0
    await todo_tool.ainvoke({"action": "add", "title": "Another task"})
    await asyncio.sleep(0.02)  # let create_task() actually schedule it
    assert len(graph_tools._todo_bg_tasks) == 1  # strong ref held while in flight

    release.set()
    for _ in range(50):
        if not graph_tools._todo_bg_tasks:
            break
        await asyncio.sleep(0.02)
    assert len(graph_tools._todo_bg_tasks) == 0  # done-callback pruned it after completion
