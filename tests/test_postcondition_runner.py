"""Agent Runtime rev.2, Faz 3 -- jarvis.execution.postcondition_runner.

One verified / one failed / one unverified case per kind, plus the
never-raises + never-silently-verified discipline. run_postconditions() is
exercised directly (no graph/tool_accounting wiring needed for these -- that
integration lives in test_tool_accounting_postconditions.py).
"""
from __future__ import annotations

import json
import sqlite3

from jarvis.execution.postcondition import PostconditionSpec
from jarvis.execution.postcondition_runner import run_postconditions


def _spec(kind: str, **params) -> PostconditionSpec:
    return PostconditionSpec(kind=kind, params=params, source="tool_contract")


def _run_one(spec, workspace=None, args=None, content=""):
    return run_postconditions((spec,), workspace=workspace, args=args or {}, tool_result_content=content)[0]


# ── file_exists ──────────────────────────────────────────────────────────────

def test_file_exists_verified(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hi")
    r = _run_one(_spec("file_exists", path_arg="path"), tmp_path, {"path": "a.txt"})
    assert r.status == "verified"


def test_file_exists_failed(tmp_path):
    r = _run_one(_spec("file_exists", path_arg="path"), tmp_path, {"path": "missing.txt"})
    assert r.status == "failed"


def test_file_exists_unverified_without_path_param():
    r = _run_one(_spec("file_exists"), None, {})
    assert r.status == "unverified"


def test_file_exists_accepts_a_literal_path(tmp_path):
    f = tmp_path / "b.txt"
    f.write_text("hi")
    r = _run_one(_spec("file_exists", path=str(f)))
    assert r.status == "verified"


# ── path_within_workspace ────────────────────────────────────────────────────

def test_path_within_workspace_verified(tmp_path):
    r = _run_one(_spec("path_within_workspace", path_arg="path"), tmp_path, {"path": "sub/a.txt"})
    assert r.status == "verified"


def test_path_within_workspace_failed_on_escape(tmp_path):
    r = _run_one(_spec("path_within_workspace", path="../outside.txt"), tmp_path, {})
    assert r.status == "failed"


def test_path_within_workspace_unverified_without_workspace():
    r = _run_one(_spec("path_within_workspace", path_arg="path"), None, {"path": "a.txt"})
    assert r.status == "unverified"


# ── file_openable ────────────────────────────────────────────────────────────

def test_file_openable_verified_plain_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"\x00\x01")
    r = _run_one(_spec("file_openable", path_arg="path"), tmp_path, {"path": "a.bin"})
    assert r.status == "verified"


def test_file_openable_failed_when_missing(tmp_path):
    r = _run_one(_spec("file_openable", path_arg="path"), tmp_path, {"path": "missing.bin"})
    assert r.status == "failed"


def test_file_openable_failed_on_corrupt_image(tmp_path):
    f = tmp_path / "fake.png"
    f.write_bytes(b"not actually a png")
    r = _run_one(_spec("file_openable", path_arg="path"), tmp_path, {"path": "fake.png"})
    assert r.status == "failed"


# ── artifact_hash_matches ────────────────────────────────────────────────────

def test_artifact_hash_matches_verified(tmp_path):
    import hashlib
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello world")
    expected = hashlib.sha256(b"hello world").hexdigest()
    r = _run_one(_spec("artifact_hash_matches", path_arg="path", expected_hash=expected), tmp_path, {"path": "a.txt"})
    assert r.status == "verified"


def test_artifact_hash_matches_failed_on_mismatch(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello world")
    r = _run_one(_spec("artifact_hash_matches", path_arg="path", expected_hash="0" * 64), tmp_path, {"path": "a.txt"})
    assert r.status == "failed"


def test_artifact_hash_matches_unverified_without_expected_hash(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(b"hi")
    r = _run_one(_spec("artifact_hash_matches", path_arg="path"), tmp_path, {"path": "a.txt"})
    assert r.status == "unverified"


# ── row_count_matches ────────────────────────────────────────────────────────

def test_row_count_matches_verified_csv(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("a,b\n1,2\n3,4\n")
    r = _run_one(_spec("row_count_matches", path_arg="path", expected_rows=3), tmp_path, {"path": "d.csv"})
    assert r.status == "verified"


def test_row_count_matches_failed_csv(tmp_path):
    f = tmp_path / "d.csv"
    f.write_text("a,b\n1,2\n3,4\n")
    r = _run_one(_spec("row_count_matches", path_arg="path", expected_rows=99), tmp_path, {"path": "d.csv"})
    assert r.status == "failed"


def test_row_count_matches_unverified_for_unknown_extension(tmp_path):
    f = tmp_path / "d.tsv"
    f.write_text("a\tb\n1\t2\n")
    r = _run_one(_spec("row_count_matches", path_arg="path", expected_rows=2), tmp_path, {"path": "d.tsv"})
    assert r.status == "unverified"


# ── series_matches ───────────────────────────────────────────────────────────

def test_series_matches_verified(tmp_path):
    manifest = tmp_path / "chart.json"
    manifest.write_text(json.dumps({"y": [1, 4, 9, 16]}))
    spec = _spec("series_matches", path_arg="path", expected_y=[1, 4, 9, 16])
    r = _run_one(spec, tmp_path, {"path": "chart.json"})
    assert r.status == "verified"


def test_series_matches_failed_on_mismatch(tmp_path):
    manifest = tmp_path / "chart.json"
    manifest.write_text(json.dumps({"y": [1, 2, 3, 4]}))
    spec = _spec("series_matches", path_arg="path", expected_y=[1, 4, 9, 16])
    r = _run_one(spec, tmp_path, {"path": "chart.json"})
    assert r.status == "failed"


def test_series_matches_unverified_when_no_manifest_exists(tmp_path):
    """The honest, currently-unreachable-in-production case: no tool writes
    a manifest today, so this must never silently claim success."""
    spec = _spec("series_matches", path_arg="path", expected_y=[1, 4, 9, 16])
    r = _run_one(spec, tmp_path, {"path": "chart.json"})
    assert r.status == "unverified"


# ── exit_code_matches ────────────────────────────────────────────────────────

def test_exit_code_matches_verified_ok():
    r = _run_one(_spec("exit_code_matches", expected_code=0), content="[OK]\nhello")
    assert r.status == "verified"


def test_exit_code_matches_verified_nonzero():
    r = _run_one(_spec("exit_code_matches", expected_code=2), content="[EXIT 2]\nboom")
    assert r.status == "verified"


def test_exit_code_matches_failed_on_mismatch():
    r = _run_one(_spec("exit_code_matches", expected_code=0), content="[EXIT 1]\nboom")
    assert r.status == "failed"


def test_exit_code_matches_unverified_with_no_marker():
    r = _run_one(_spec("exit_code_matches", expected_code=0), content="some unrelated text")
    assert r.status == "unverified"


# ── record_exists ────────────────────────────────────────────────────────────

def _make_db(tmp_path, rows):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE items (id TEXT PRIMARY KEY, name TEXT)")
    conn.executemany("INSERT INTO items VALUES (?, ?)", rows)
    conn.commit()
    conn.close()
    return db


def test_record_exists_verified(tmp_path):
    db = _make_db(tmp_path, [("abc123", "widget")])
    spec = _spec("record_exists", db_path=str(db), table="items", id_column="id", id_arg="item_id")
    r = _run_one(spec, tmp_path, {"item_id": "abc123"})
    assert r.status == "verified"


def test_record_exists_failed_when_absent(tmp_path):
    db = _make_db(tmp_path, [("abc123", "widget")])
    spec = _spec("record_exists", db_path=str(db), table="items", id_column="id", id_arg="item_id")
    r = _run_one(spec, tmp_path, {"item_id": "does-not-exist"})
    assert r.status == "failed"


def test_record_exists_rejects_non_identifier_table_name(tmp_path):
    """Defense in depth against SQL injection via a malformed table/column
    name -- even though these always come from static ToolSpec config, never
    live user/model input."""
    db = _make_db(tmp_path, [("abc123", "widget")])
    spec = _spec("record_exists", db_path=str(db), table="items; DROP TABLE items", id_column="id", id_arg="item_id")
    r = _run_one(spec, tmp_path, {"item_id": "abc123"})
    assert r.status == "unverified"
    # and the table really is untouched
    conn = sqlite3.connect(str(db))
    assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
    conn.close()


def test_record_exists_unverified_missing_params(tmp_path):
    r = _run_one(_spec("record_exists"), tmp_path, {})
    assert r.status == "unverified"


# ── cross-cutting: unknown kind + never-raises ──────────────────────────────

def test_run_postconditions_reports_multiple_specs_independently(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("hi")
    specs = (
        _spec("file_exists", path_arg="path"),
        _spec("row_count_matches", path_arg="path", expected_rows=999),
    )
    results = run_postconditions(specs, workspace=tmp_path, args={"path": "ok.txt"}, tool_result_content="")
    assert len(results) == 2
    assert results[0].status == "verified"       # file_exists
    assert results[1].status == "unverified"      # .txt has no row reader


def test_run_postconditions_never_raises_even_on_internal_error(monkeypatch, tmp_path):
    import jarvis.execution.postcondition_runner as runner_mod

    def _boom(spec, workspace, args, content):
        raise RuntimeError("simulated bug in a check")
    monkeypatch.setitem(runner_mod._RUNNERS, "file_exists", _boom)

    results = run_postconditions(
        (_spec("file_exists", path_arg="path"),), workspace=tmp_path, args={"path": "x"}, tool_result_content="",
    )
    assert results[0].status == "unverified"
    assert "simulated bug" in results[0].detail
