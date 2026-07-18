"""Faz 1.3 — plot_data must accept inline data (the B6 root cause).

The ``data`` domain the router opens for "grafik çiz" exposes no file-writing
tool, so numbers the user typed ("1, 4, 9, 16") had no way onto disk for the
file-only plot_data — the model hallucinated a path, the tool returned
``[ERROR] Data file not found``, and JARVIS still claimed success. Inline data
via ``data_json`` closes that: a real PNG, or an honest error.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.config import Settings
from jarvis.graph import tools as graph_tools
from jarvis.memory import Memory
from jarvis.tools.plotting import frame_from_inline


# ── frame_from_inline: pure parsing ──────────────────────────────────────────

def test_inline_bare_array():
    df, x, y, err = frame_from_inline("[1,4,9,16]", "", "")
    assert err == "" and x == "x" and y == "y"
    assert list(df["y"]) == [1, 4, 9, 16]
    assert list(df["x"]) == [0, 1, 2, 3]


def test_inline_single_series_object():
    df, x, y, err = frame_from_inline('{"y": [1,4,9,16]}', "", "")
    assert err == "" and x == "x" and y == "y"
    assert list(df["y"]) == [1, 4, 9, 16]


def test_inline_xy_object():
    df, x, y, err = frame_from_inline('{"x":[1,2,3,4],"y":[1,4,9,16]}', "", "")
    assert err == "" and x == "x" and y == "y"
    assert list(df["x"]) == [1, 2, 3, 4]


def test_inline_two_unnamed_columns_infer_axes():
    df, x, y, err = frame_from_inline('{"ay":["Oca","Sub"],"satis":[10,20]}', "", "")
    assert err == "" and x == "ay" and y == "satis"


def test_inline_bad_json():
    df, x, y, err = frame_from_inline("not json", "", "")
    assert df is None and err.startswith("[ERROR]")


# ── Round 3 guardrails: data_json is model-generated, so it gets hard limits ──

def test_inline_rejects_oversize_payload():
    big = "[" + ",".join(["1"] * 200_000) + "]"  # ~400 KB of JSON text
    df, _, _, err = frame_from_inline(big, "", "")
    assert df is None and err.startswith("[ERROR]") and "KB" in err


def test_inline_rejects_too_many_rows():
    import json
    df, _, _, err = frame_from_inline(json.dumps(list(range(10_001))), "", "")
    assert df is None and "10000" in err.replace(",", "")

    too_long_col = json.dumps({"y": list(range(10_001))})
    df2, _, _, err2 = frame_from_inline(too_long_col, "", "")
    assert df2 is None and err2.startswith("[ERROR]")


def test_inline_rejects_too_many_columns():
    import json
    df, _, _, err = frame_from_inline(json.dumps({f"c{i}": [1] for i in range(101)}), "", "")
    assert df is None and "columns" in err


def test_inline_rejects_nested_structures():
    df, _, _, err = frame_from_inline('[{"a": 1}, {"a": 2}]', "", "")
    assert df is None and err.startswith("[ERROR]")

    df2, _, _, err2 = frame_from_inline('{"y": [[1, 2], [3, 4]]}', "", "")
    assert df2 is None and "nested" in err2

    df3, _, _, err3 = frame_from_inline('{"meta": {"a": 1}}', "", "")
    assert df3 is None and "nested" in err3


# ── plot_data tool: inline → real PNG under the workspace ────────────────────

def _plot_tool(tmp_path):
    settings = Settings()
    memory = Memory(settings)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = next(t for t in graph_tools.make_tools(workspace, settings, memory)
                if t.name == "plot_data")
    return tool, workspace


def test_inline_produces_png(isolated_cwd, tmp_path):
    tool, workspace = _plot_tool(tmp_path)
    result = tool.invoke({"kind": "line", "data_json": "[1,4,9,16]", "title": "B6"})
    assert not result.startswith("[ERROR]"), result
    png = Path(result)
    assert png.exists() and png.suffix == ".png"
    # Lands under the workspace, not a hallucinated path.
    assert workspace.resolve() in png.resolve().parents


def test_no_source_is_honest_error(isolated_cwd, tmp_path):
    tool, _ = _plot_tool(tmp_path)
    result = tool.invoke({"kind": "line"})
    assert result.startswith("[ERROR]")
