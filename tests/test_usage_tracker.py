"""usage.py — token/cost tracking, including the Faz 8 cross-process clobber fix.

UsageTracker.record() used to hold self._total in memory for the process's
whole life and blindly overwrite data/usage.json with that stale snapshot on
every save -- across two live processes (e.g. the CLI plus a long-running
`--api` server), whichever saved last silently erased the other's recorded
spend. Two UsageTracker instances pointed at the same file (below) reproduce
that exactly: each has its own private self._total, the same relationship
two separate OS processes would have.
"""
from __future__ import annotations

import json

from jarvis.usage import UsageTracker


def test_record_updates_session_and_total(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    tracker.record("gemini-2.5-flash", 100, 50)

    assert tracker.session_cost > 0
    assert tracker.total_cost == tracker.session_cost  # first-ever record


def test_zero_token_call_is_a_no_op(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    tracker.record("gemini-2.5-flash", 0, 0)
    assert tracker.session_cost == 0.0
    assert not (tmp_path / "usage.json").exists()  # never even wrote the file


def test_pro_tier_costs_more_than_flash_for_same_tokens(tmp_path):
    flash = UsageTracker(tmp_path / "flash.json")
    flash.record("gemini-2.5-flash", 1000, 1000)
    pro = UsageTracker(tmp_path / "pro.json")
    pro.record("gemini-2.5-pro", 1000, 1000)
    assert pro.session_cost > flash.session_cost


def test_two_concurrent_trackers_do_not_clobber_each_other(tmp_path):
    """BUG-usage regression: simulates two processes (CLI + `--api`) recording
    against the same usage.json around the same time."""
    usage_path = tmp_path / "usage.json"
    process_a = UsageTracker(usage_path)
    process_b = UsageTracker(usage_path)  # opened "at the same time" as A

    process_a.record("gemini-2.5-flash", 1000, 200)
    process_b.record("gemini-2.5-flash", 500, 100)  # B never saw A's in-process update

    on_disk = json.loads(usage_path.read_text(encoding="utf-8"))
    assert on_disk["tokens_in"] == 1500, "B's write clobbered A's contribution"
    assert on_disk["tokens_out"] == 300
    assert on_disk["flash_turns"] == 2


def test_a_third_write_builds_on_the_other_processes_write(tmp_path):
    usage_path = tmp_path / "usage.json"
    process_a = UsageTracker(usage_path)
    process_b = UsageTracker(usage_path)

    process_a.record("gemini-2.5-flash", 1000, 0)
    process_b.record("gemini-2.5-flash", 500, 0)
    process_a.record("gemini-2.5-pro", 10, 0)  # A again, using its own stale self._total

    on_disk = json.loads(usage_path.read_text(encoding="utf-8"))
    assert on_disk["tokens_in"] == 1510
    assert on_disk["flash_turns"] == 2
    assert on_disk["pro_turns"] == 1


def test_session_counters_stay_process_local(tmp_path):
    """Unlike the all-time total, session_cost must NOT pick up another
    tracker's activity -- it resets per JarvisAgent instantiation by design.
    (total_cost is a snapshot as of this instance's own last record() call,
    not a live re-read -- only the on-disk file is guaranteed to merge both,
    which test_two_concurrent_trackers_do_not_clobber_each_other checks.)"""
    usage_path = tmp_path / "usage.json"
    process_a = UsageTracker(usage_path)
    process_b = UsageTracker(usage_path)

    process_a.record("gemini-2.5-flash", 1000, 0)
    process_b.record("gemini-2.5-flash", 500, 0)

    assert process_a.session_cost != process_b.session_cost
    assert process_a.session_cost > process_b.session_cost  # A recorded 2x B's tokens


def test_report_includes_both_session_and_alltime(tmp_path):
    tracker = UsageTracker(tmp_path / "usage.json")
    tracker.record("gemini-2.5-flash", 100, 50)
    text = tracker.report()
    assert "Session" in text
    assert "All-time" in text
