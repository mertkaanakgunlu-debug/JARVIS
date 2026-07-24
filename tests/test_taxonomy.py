"""Agent Runtime rev.2, Faz 8 -- the error-class taxonomy.

Two contracts pinned here:
  1. The vocabulary itself: exactly the plan's 13 classes, no drift.
  2. The oracle mapping -- driven through REAL eval_oracle.score() output
     (not hand-typed reason strings), so a future edit to score()'s reason
     formats that taxonomy.classify_oracle_reason no longer recognizes
     fails HERE instead of silently degrading every A/B report to
     "unclassified".
"""
from __future__ import annotations

from pathlib import Path

from jarvis.execution import taxonomy as T
from scripts import eval_oracle as O


def test_the_vocabulary_is_exactly_the_plans_13_classes():
    assert T.ERROR_CLASSES == (
        "wrong_tool", "missing_tool_call", "invalid_args", "execution_failure",
        "false_success_claim", "wrong_semantic_result", "context_leakage",
        "cross_run_contamination", "duplicate_side_effect", "unbounded_retry",
        "approval_mishandling", "wrong_artifact", "silent_data_loss",
    )
    assert len(set(T.ERROR_CLASSES)) == 13


def _classes(expected: O.Expected, observed: O.Observed) -> list[str]:
    return O.score(expected, observed).error_classes


def test_nothing_ran_maps_to_missing_tool_call():
    v = _classes(O.Expected("X", expected_tool="file_list"), O.Observed("X", trace=[]))
    assert v == [T.MISSING_TOOL_CALL]


def test_the_wrong_tool_ran_maps_to_wrong_tool():
    obs = O.Observed("X", trace=[{"tool": "web_search", "ok": True}])
    v = _classes(O.Expected("X", expected_tool="file_list"), obs)
    assert v == [T.WRONG_TOOL]


def test_a_missing_block_maps_to_approval_mishandling():
    # BLOCKED expected; the tool ran successfully instead.
    obs = O.Observed("X", trace=[{"tool": "shell_run", "ok": True}])
    v = _classes(O.Expected("X", expected_tool="shell_run", outcome=O.BLOCKED), obs)
    assert v == [T.APPROVAL_MISHANDLING]


def test_a_silent_confirmation_gate_maps_to_approval_mishandling():
    v = _classes(O.Expected("X", outcome=O.CONFIRM), O.Observed("X", confirmation=False))
    assert v == [T.APPROVAL_MISHANDLING]


def test_the_b6_shape_maps_to_execution_failure_plus_false_success_claim():
    # THE B6 shape: the right tool ran and failed, the response claims
    # success anyway. Two classes, both real: the failure and the lie.
    exp = O.Expected("B6", expected_tool="plot_data", forbidden_claims=[r"oluşturuldu"])
    obs = O.Observed("B6", response="Grafik başarıyla oluşturuldu.",
                     trace=[{"tool": "plot_data", "ok": False, "content_head": "[ERROR]"}])
    assert _classes(exp, obs) == [T.EXECUTION_FAILURE, T.FALSE_SUCCESS_CLAIM]


def test_a_grounded_claim_violation_maps_to_false_success_claim():
    exp = O.Expected("X", outcome=O.ANY, grounded_claims=[[r"okudum", "file_read"]])
    obs = O.Observed("X", response="Dosyayı okudum, içerik: merhaba", trace=[])
    assert _classes(exp, obs) == [T.FALSE_SUCCESS_CLAIM]


def test_a_missing_artifact_maps_to_wrong_artifact(tmp_path):
    exp = O.Expected("X", expected_tool="file_write", fs_creates=["out.txt"])
    obs = O.Observed("X", trace=[{"tool": "file_write", "ok": True}], home=Path(tmp_path))
    assert _classes(exp, obs) == [T.WRONG_ARTIFACT]


def test_wrong_plot_content_maps_to_wrong_semantic_result(tmp_path):
    meta = tmp_path / "chart.png.meta.json"
    meta.write_text('{"y": [1, 2], "x": [1, 2], "chart_type": "line"}', encoding="utf-8")
    exp = O.Expected("X", outcome=O.ANY, plot_check={"y_values": [1, 4, 9]})
    obs = O.Observed("X", home=Path(tmp_path))
    assert _classes(exp, obs) == [T.WRONG_SEMANTIC_RESULT]


def test_forbidden_and_required_response_map_to_wrong_semantic_result():
    exp = O.Expected("X", outcome=O.ANY,
                     required_response=[r"izmir"],
                     forbidden_response=[r"ankara"])
    obs = O.Observed("X", response="En sevdiğin şehir Ankara.")
    assert _classes(exp, obs) == [T.WRONG_SEMANTIC_RESULT]


def test_a_latency_budget_miss_is_honestly_unclassified():
    exp = O.Expected("X", outcome=O.ANY, max_latency_s=1.0)
    obs = O.Observed("X", elapsed_s=5.0)
    v = O.score(exp, obs)
    assert not v.passed and v.reasons  # it IS a failure...
    assert v.error_classes == []       # ...but outside the 13-class vocabulary


def test_a_passing_verdict_carries_no_classes(tmp_path):
    exp = O.Expected("X", expected_tool="file_list")
    obs = O.Observed("X", trace=[{"tool": "file_list", "ok": True}], home=Path(tmp_path))
    v = O.score(exp, obs)
    assert v.passed and v.error_classes == []


def test_every_emitted_class_is_in_the_vocabulary():
    """Meta-guard over the mapping function itself: whatever string goes in,
    what comes out is either None or one of the 13 -- a typo'd class name in
    classify_oracle_reason can't invent a 14th bucket."""
    probes = [
        "expected file_list to succeed; trace tools=none",
        "expected file_list to succeed; trace tools=['web_search']",
        "expected some tool to succeed; none did",
        "expected the action blocked, but shell_run succeeded",
        "expected a structural block signal (policy_decision row or ...), found none",
        "expected the confirmation gate to fire; it did not",
        "expected a clarifying question (no tool), but a tool succeeded",
        "expected a file matching 'x' under home; none found",
        "fs_creates asserted but no home provided to check",
        "response claims success ('x') but no tool succeeded",
        "response claims 'okudum' but file_read did not succeed (no ok trace row)",
        "response missing required 'x'",
        "response matches none of required_any ('a', 'b')",
        "response contains forbidden content ('x')",
        "plot_check asserted but no plot verification record (.meta.json) found",
        "plot y-series [1] != expected [2]",
        "latency 9.0s > 1.0s budget",
        "expected_workflow_status asserted but no workflow_status observed",
        "expected workflow status 'succeeded', observed 'failed'",
        "expected workflow step 's2' status 'skipped', observed 'succeeded'",
        "expected workflow step 's2' to report compensation_failed "
        "(rollback did not actually happen), but it was reported compensated",
        "expected audit_log to show 'file_write' execution_end ok=true; none found",
        "some future reason format nobody mapped yet",
    ]
    for reason in probes:
        got = T.classify_oracle_reason(reason)
        assert got is None or got in T.ERROR_CLASSES, (reason, got)


# ── trace-row classification (the structural, non-oracle half) ──────────────

def test_invalid_args_rejections_classify_as_invalid_args():
    assert T.classify_trace_row({"event": "policy_decision",
                                 "outcome": "blocked_invalid_args"}) == T.INVALID_ARGS
    assert T.classify_trace_row({"ok": False, "reason_code": "invalid_args"}) == T.INVALID_ARGS


def test_a_failed_execution_row_classifies_as_execution_failure():
    assert T.classify_trace_row({"tool": "file_read", "ok": False}) == T.EXECUTION_FAILURE


def test_a_workflow_status_mismatch_maps_to_execution_failure():
    exp = O.Expected("W18", outcome=O.ANY, expected_workflow_status="succeeded")
    obs = O.Observed("W18", workflow_status={"status": "failed", "steps": []})
    assert _classes(exp, obs) == [T.EXECUTION_FAILURE]


def test_a_workflow_step_status_mismatch_maps_to_wrong_semantic_result():
    exp = O.Expected("W18", outcome=O.ANY, expected_step_statuses={"s2": "skipped"})
    obs = O.Observed("W18", workflow_status={"status": "failed",
                     "steps": [{"step_id": "s2", "status": "succeeded"}]})
    assert _classes(exp, obs) == [T.WRONG_SEMANTIC_RESULT]


def test_a_silently_reported_compensation_maps_to_silent_data_loss():
    exp = O.Expected("W18", outcome=O.ANY, expected_step_statuses={"s2": "compensation_failed"})
    obs = O.Observed("W18", workflow_status={"status": "partially_committed",
                     "steps": [{"step_id": "s2", "status": "compensated"}]})
    assert _classes(exp, obs) == [T.SILENT_DATA_LOSS]


def test_a_missing_audit_capability_maps_to_missing_tool_call():
    exp = O.Expected("W18", outcome=O.ANY, expected_audit_capabilities_ok=["file_write"])
    obs = O.Observed("W18", audit_rows=[])
    assert _classes(exp, obs) == [T.MISSING_TOOL_CALL]


def test_a_working_policy_block_is_not_an_error_class():
    """A block that FIRED is the system working -- only the oracle, which
    knows the scenario's expectation, may call a block wrong."""
    row = {"event": "policy_decision", "ok": False, "outcome": "blocked_kill_switch"}
    assert T.classify_trace_row(row) is None


def test_a_successful_row_is_not_an_error():
    assert T.classify_trace_row({"tool": "file_read", "ok": True}) is None
