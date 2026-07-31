"""Post-MVP Faz 1 -- the shadow->enforce promotion gate as a measurement.

The plan's rule for turning execution_contract_mode up is *"100 gercek
artifact isleminde 0 false block gorulmeden enforce yok"*. These tests pin
the two things that make that rule real rather than rhetorical: the
population being counted is artifact operations (not every tool call, which
would clear 100 in an afternoon and mean nothing), and false positives are
counted only when a human reports one -- never inferred, and never silently
treated as zero-because-clean.
"""
from __future__ import annotations

import json

from jarvis.execution import rollout


def test_a_fresh_install_summarizes_to_all_zeroes(rollout_metrics_file):
    assert not rollout_metrics_file.exists()
    s = rollout.summarize()
    assert s["verification_total"] == 0
    assert s["artifact_operations"] == 0
    assert s["false_positive_known"] == 0


def test_verification_rows_aggregate_by_verdict(rollout_metrics_file):
    for verdict in ("verified", "verified", "unverified", "failed", "not_applicable"):
        rollout.record_verification(
            mode="shadow", capability="plot_data", display_status="confirmed",
            postcondition_verdict=verdict, artifacts_declared=1,
        )
    s = rollout.summarize()
    assert s["verification_total"] == 5
    assert (s["verified"], s["unverified"], s["failed"], s["not_applicable"]) == (2, 1, 1, 1)


def test_only_operations_that_declared_an_artifact_count_toward_the_gate(rollout_metrics_file):
    rollout.record_verification(
        mode="shadow", capability="plot_data", display_status="confirmed",
        postcondition_verdict="verified", artifacts_declared=2,
    )
    rollout.record_verification(
        mode="shadow", capability="web_search", display_status="reported_success_unverified",
        postcondition_verdict="not_applicable", artifacts_declared=0,
    )
    s = rollout.summarize()
    assert s["verification_total"] == 2
    assert s["artifact_operations"] == 1


def test_claim_gate_rows_separate_fired_repaired_and_blocked(rollout_metrics_file):
    rollout.record_claim_gate(mode="shadow", enforced=False, fired=False)
    rollout.record_claim_gate(mode="enforce_reversible", enforced=True, fired=True, repaired=True)
    rollout.record_claim_gate(
        mode="enforce_reversible", enforced=True, fired=True, repaired=False, blocked=True,
    )
    s = rollout.summarize()
    assert s["claim_gate_fired"] == 2
    assert s["claim_gate_repaired"] == 1
    assert s["claim_gate_blocked"] == 1


def test_the_gate_is_not_ready_before_the_threshold(rollout_metrics_file):
    for _ in range(rollout.ENFORCE_GATE_MIN_ARTIFACT_OPERATIONS - 1):
        rollout.record_verification(
            mode="shadow", capability="plot_data", display_status="confirmed",
            postcondition_verdict="verified", artifacts_declared=1,
        )
    status = rollout.enforce_gate_status()
    assert status["ready"] is False
    assert status["artifact_operations"] == rollout.ENFORCE_GATE_MIN_ARTIFACT_OPERATIONS - 1


def test_the_gate_opens_at_the_threshold_with_no_reported_false_blocks(rollout_metrics_file):
    for _ in range(rollout.ENFORCE_GATE_MIN_ARTIFACT_OPERATIONS):
        rollout.record_verification(
            mode="shadow", capability="plot_data", display_status="confirmed",
            postcondition_verdict="verified", artifacts_declared=1,
        )
    assert rollout.enforce_gate_status()["ready"] is True


def test_one_reported_false_positive_closes_the_gate_again(rollout_metrics_file):
    for _ in range(rollout.ENFORCE_GATE_MIN_ARTIFACT_OPERATIONS):
        rollout.record_verification(
            mode="shadow", capability="plot_data", display_status="confirmed",
            postcondition_verdict="verified", artifacts_declared=1,
        )
    rollout.mark_false_positive(note="blocked a chart that was really written")
    status = rollout.enforce_gate_status()
    assert status["false_positive_known"] == 1
    assert status["ready"] is False


def test_the_status_states_that_zero_means_unreported_not_clean(rollout_metrics_file):
    # An operator reading `false_positive_known: 0` must not read it as
    # "verification has never been wrong" -- see rollout.py's docstring.
    caveat = rollout.enforce_gate_status()["caveat"]
    assert "never inferred" in caveat
    assert "none reported" in caveat


def test_a_corrupt_line_does_not_break_the_summary(rollout_metrics_file):
    rollout.record_verification(
        mode="shadow", capability="plot_data", display_status="confirmed",
        postcondition_verdict="verified", artifacts_declared=1,
    )
    with rollout_metrics_file.open("a", encoding="utf-8") as f:
        f.write("{not json at all\n\n")
    assert rollout.summarize()["verification_total"] == 1


def test_recording_never_raises_when_the_stream_is_unwritable(monkeypatch, tmp_path):
    # Same discipline as audit_log: a metric that can break a turn is worse
    # than no metric.
    monkeypatch.setattr(rollout, "_path", lambda: tmp_path / "no" / "such" / "dir" / "x.jsonl")
    monkeypatch.setattr(
        rollout.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("nope"))
    )
    rollout.record_claim_gate(mode="shadow", enforced=False, fired=True)  # must not raise


def test_rows_are_append_only_and_timestamped(rollout_metrics_file):
    rollout.record_verification(
        mode="shadow", capability="a", display_status="confirmed",
        postcondition_verdict="verified",
    )
    rollout.record_verification(
        mode="shadow", capability="b", display_status="confirmed",
        postcondition_verdict="verified",
    )
    rows = [json.loads(x) for x in rollout_metrics_file.read_text(encoding="utf-8").splitlines()]
    assert [r["capability"] for r in rows] == ["a", "b"]
    assert all(r["ts"] for r in rows)
