"""Agent Runtime rev.2, Faz 5 -- jarvis/run_context.py.

RunContext gives the run/turn and artifact scopes (reviewer #8's scope
separation) a real identity instead of ad hoc string-building at each call
site. for_turn() is the agent/graph-level constructor (reuses the exact
LangGraph thread_id string); for_execution() is the fallback for a plain
@tool closure with no per-turn state access -- see its own docstring for
why. write_run_manifest() is the replay record built on top of either.
"""
from __future__ import annotations

import json

from jarvis.run_context import RunContext, write_run_manifest


# ── RunContext construction ─────────────────────────────────────────────────

def test_for_turn_reuses_the_langgraph_thread_id_shape(tmp_path):
    ctx = RunContext.for_turn(tmp_path, "20260722-abcd", 3)
    assert ctx.run_id == "20260722-abcd-t3"


def test_for_turn_artifact_dir_is_under_workspace_data_runs(tmp_path):
    ctx = RunContext.for_turn(tmp_path, "s1", 1)
    assert ctx.artifact_dir == tmp_path / "data" / "runs" / "s1-t1"


def test_for_turn_same_session_and_turn_is_deterministic(tmp_path):
    """Same identity every call -- callers can correlate a run_id back to a
    specific graph checkpoint after the fact."""
    a = RunContext.for_turn(tmp_path, "s1", 2)
    b = RunContext.for_turn(tmp_path, "s1", 2)
    assert a.run_id == b.run_id == "s1-t2"


def test_for_execution_ids_are_unique_across_calls(tmp_path):
    """The concrete fix for plot.png colliding: two calls with nothing else
    to distinguish them must still get distinct homes."""
    a = RunContext.for_execution(tmp_path)
    b = RunContext.for_execution(tmp_path)
    assert a.run_id != b.run_id
    assert a.artifact_dir != b.artifact_dir


def test_for_execution_artifact_dir_is_under_workspace_data_runs(tmp_path):
    ctx = RunContext.for_execution(tmp_path)
    assert ctx.artifact_dir.parent == tmp_path / "data" / "runs"


def test_neither_constructor_creates_the_directory(tmp_path):
    """Lazy-mkdir convention -- constructing a RunContext must not touch disk."""
    RunContext.for_turn(tmp_path, "s1", 1)
    RunContext.for_execution(tmp_path)
    assert not (tmp_path / "data").exists()


# ── write_run_manifest ──────────────────────────────────────────────────────

def test_write_run_manifest_creates_the_file_and_dir(tmp_path):
    ctx = RunContext.for_turn(tmp_path, "s1", 1)
    path = write_run_manifest(ctx, model="qwen3:8b", temperature=0.0)

    assert path == ctx.artifact_dir / "run_manifest.json"
    assert path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "s1-t1"
    assert data["model"] == "qwen3:8b"
    assert data["temperature"] == 0.0
    assert "written_at" in data


def test_write_run_manifest_accepts_arbitrary_fields(tmp_path):
    """Open-ended **fields on purpose -- no single call site holds the plan's
    full field list yet (see this module's own docstring)."""
    ctx = RunContext.for_execution(tmp_path)
    path = write_run_manifest(
        ctx,
        tool_subset={"primary_domain": "data", "tool_names": ["plot_data"]},
        execution_envelopes=[{"capability": "plot_data", "status": "success"}],
        input_digest="abc123",
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["tool_subset"]["primary_domain"] == "data"
    assert data["execution_envelopes"][0]["capability"] == "plot_data"
    assert data["input_digest"] == "abc123"


def test_write_run_manifest_handles_none_values(tmp_path):
    """agent.py passes None for fields it genuinely doesn't have this turn
    (e.g. no trace yet) -- must serialize cleanly, not raise."""
    ctx = RunContext.for_turn(tmp_path, "s1", 1)
    path = write_run_manifest(ctx, model=None, tool_subset=None)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["model"] is None
    assert data["tool_subset"] is None


def test_write_run_manifest_never_raises_on_unwritable_path(tmp_path, monkeypatch):
    """Same 'auxiliary record can't take down the turn' discipline as
    audit_log.record()/tool_trace.record()."""
    ctx = RunContext.for_turn(tmp_path, "s1", 1)

    def _boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(type(ctx.artifact_dir), "mkdir", _boom)

    path = write_run_manifest(ctx, model="x")  # must not raise
    assert path == ctx.artifact_dir / "run_manifest.json"
    assert not path.exists()


def test_two_runs_do_not_clobber_each_others_manifest(tmp_path):
    ctx1 = RunContext.for_turn(tmp_path, "s1", 1)
    ctx2 = RunContext.for_turn(tmp_path, "s1", 2)
    write_run_manifest(ctx1, model="turn-1")
    write_run_manifest(ctx2, model="turn-2")

    assert json.loads((ctx1.artifact_dir / "run_manifest.json").read_text())["model"] == "turn-1"
    assert json.loads((ctx2.artifact_dir / "run_manifest.json").read_text())["model"] == "turn-2"
