"""manual_test_driver._latest_workflow_id() -- how a workflow scenario
finds the workflow it just created.

Review finding (2026-07-25): the bound used to be a second-resolution
timestamp (`time.strftime(...)` compared with `row["ts"] >= since`). Two
workflows created inside the same wall-clock second are indistinguishable
to that comparison, so W18 could be scored against the wrong workflow's
status and audit rows. audit_log.jsonl is append-only, so its length taken
before the request is an exact bound regardless of clock resolution.
"""
from __future__ import annotations

import json

import pytest

from scripts import alpha_gate as G

# Imported through alpha_gate's guarded loader, which shields pytest's
# stdout from the driver's console-encoding rewrap (see _load_driver()).
D = G._load_driver()


@pytest.fixture
def audit_log(tmp_path, monkeypatch):
    """Point the driver's audit-log reader at a temp file and hand back an
    appender, so tests write rows the way the server would."""
    home_data = tmp_path / "data"
    home_data.mkdir()
    monkeypatch.setattr(D, "HOME_DATA", home_data)
    path = home_data / "audit_log.jsonl"

    def append(**row):
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")

    return append


def test_finds_the_workflow_created_after_the_bound(audit_log):
    audit_log(ts="2026-07-25T10:00:00", workflow_id="wf-old")
    since = D.audit_log_len()
    audit_log(ts="2026-07-25T10:00:05", workflow_id="wf-new")

    assert D._latest_workflow_id(since) == "wf-new"


def test_ignores_everything_recorded_before_the_bound(audit_log):
    """A turn where the model never called workflow_start must honestly
    report "none found", not inherit an earlier scenario's workflow."""
    audit_log(ts="2026-07-25T10:00:00", workflow_id="wf-old")
    since = D.audit_log_len()

    assert D._latest_workflow_id(since) is None


@pytest.mark.parametrize("ts", ["2026-07-25T10:00:00", "2026-07-25T10:00:01"])
def test_same_second_workflows_are_distinguished_by_position(audit_log, ts):
    """THE regression. Both rows share a timestamp: the old `ts >= since`
    bound admitted the earlier workflow too, and sorting on an equal key
    could return it instead of the one this turn actually created. The
    answer must be decided by position in an append-only file, not clock."""
    audit_log(ts=ts, workflow_id="wf-someone-elses")
    since = D.audit_log_len()
    audit_log(ts=ts, workflow_id="wf-mine")

    assert D._latest_workflow_id(since) == "wf-mine"


def test_the_newest_qualifying_workflow_wins_when_a_turn_creates_two(audit_log):
    since = D.audit_log_len()
    audit_log(ts="2026-07-25T10:00:01", workflow_id="wf-first")
    audit_log(ts="2026-07-25T10:00:01", workflow_id="wf-second")

    assert D._latest_workflow_id(since) == "wf-second"


def test_rows_without_a_workflow_id_are_skipped(audit_log):
    since = D.audit_log_len()
    audit_log(ts="2026-07-25T10:00:01", workflow_id="wf-mine")
    audit_log(ts="2026-07-25T10:00:02", tool="file_write")  # ordinary audit row

    assert D._latest_workflow_id(since) == "wf-mine"


def test_an_empty_log_is_not_an_error(audit_log):
    assert D._latest_workflow_id(0) is None


def test_audit_log_len_counts_rows(audit_log):
    assert D.audit_log_len() == 0
    audit_log(ts="2026-07-25T10:00:00", workflow_id="wf-1")
    audit_log(ts="2026-07-25T10:00:01", tool="file_read")
    assert D.audit_log_len() == 2
