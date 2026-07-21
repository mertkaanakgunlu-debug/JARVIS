"""Agent Runtime rev.2, Faz 4 -- jarvis/execution/summary.py.

VerifiedExecutionSummary is the deterministic, code-authored verdict compose_
node now composes from instead of trusting the model to narrate raw tool
output. These tests exercise the pure functions directly (build_verified_
summary / the two renderers / audit_claims); tests/test_verified_response_
composition.py covers compose_node's own wiring (mode gating, the
unconditional append, the secondary audit log call).
"""
from __future__ import annotations

from jarvis.execution.envelope import ExecutionEnvelope
from jarvis.execution.postcondition import PostconditionResult, PostconditionSpec
from jarvis.execution.summary import (
    audit_claims,
    build_verified_summary,
    render_operation_status_for_model,
    render_operation_status_for_user,
)


def _spec(kind="file_exists", severity="required") -> PostconditionSpec:
    return PostconditionSpec(kind=kind, params={}, severity=severity, source="tool_contract")


def _envelope(
    *, capability="file_write", status="success", execution_id="call_0",
    normalized_output="ok", postconditions=None,
) -> dict:
    return ExecutionEnvelope(
        execution_id=execution_id,
        capability=capability,
        status=status,
        inputs_digest="digest",
        normalized_output=normalized_output,
        postconditions=postconditions or [],
    ).model_dump()


# ── build_verified_summary / display_status derivation ─────────────────────

def test_empty_envelopes_produce_empty_summary():
    summary = build_verified_summary([])
    assert summary.operations == []
    assert summary.any_failed is False
    assert summary.any_unverified is False
    assert summary.all_confirmed is False  # vacuously false, not vacuously true


def test_success_with_no_postconditions_is_reported_unverified():
    summary = build_verified_summary([_envelope(status="success", postconditions=[])])
    op = summary.operations[0]
    assert op.postcondition_verdict == "not_applicable"
    assert op.display_status == "reported_success_unverified"
    assert summary.any_unverified is True
    assert summary.any_failed is False


def test_success_with_verified_required_postcondition_is_confirmed():
    pc = [PostconditionResult(spec=_spec(severity="required"), status="verified", detail="")]
    summary = build_verified_summary([_envelope(status="success", postconditions=pc)])
    op = summary.operations[0]
    assert op.postcondition_verdict == "verified"
    assert op.display_status == "confirmed"
    assert summary.all_confirmed is True
    assert summary.any_failed is False


def test_success_with_failed_required_postcondition_is_the_b6_class_case():
    """The tool self-reported ok, but independent verification disagrees --
    exactly the class of bug this initiative exists to catch."""
    pc = [PostconditionResult(spec=_spec(severity="required"), status="failed", detail="missing")]
    summary = build_verified_summary([_envelope(status="success", postconditions=pc)])
    op = summary.operations[0]
    assert op.postcondition_verdict == "failed"
    assert op.display_status == "reported_success_verification_failed"
    assert summary.any_failed is True


def test_only_warning_severity_postcondition_never_gates_success():
    pc = [PostconditionResult(spec=_spec(severity="warning"), status="failed", detail="cosmetic")]
    summary = build_verified_summary([_envelope(status="success", postconditions=pc)])
    op = summary.operations[0]
    assert op.postcondition_verdict == "not_applicable"
    assert op.display_status == "reported_success_unverified"
    assert summary.any_failed is False


def test_unverified_required_postcondition_with_no_failure_stays_unverified():
    pc = [PostconditionResult(spec=_spec(severity="required"), status="unverified", detail="no workspace")]
    summary = build_verified_summary([_envelope(status="success", postconditions=pc)])
    op = summary.operations[0]
    assert op.postcondition_verdict == "unverified"
    assert op.display_status == "reported_success_unverified"


def test_tool_reported_failure_is_failed_regardless_of_postconditions():
    pc = [PostconditionResult(spec=_spec(severity="required"), status="verified", detail="")]
    summary = build_verified_summary([_envelope(status="failed", postconditions=pc)])
    assert summary.operations[0].display_status == "failed"
    assert summary.any_failed is True


def test_blocked_invalid_args_and_timed_out_all_map_to_failed():
    for status in ("blocked", "invalid_args", "timed_out"):
        summary = build_verified_summary([_envelope(status=status)])
        assert summary.operations[0].display_status == "failed", status


def test_partial_status_maps_to_partial():
    summary = build_verified_summary([_envelope(status="partial")])
    assert summary.operations[0].display_status == "partial"
    assert summary.any_failed is True


def test_malformed_envelope_is_dropped_not_fatal():
    good = _envelope(capability="file_write")
    bad = {"not_a_real_envelope": True}
    summary = build_verified_summary([bad, good])
    assert len(summary.operations) == 1
    assert summary.operations[0].capability == "file_write"


def test_failed_capabilities_lists_only_the_non_success_ones():
    ok = _envelope(capability="file_write", status="success", execution_id="a",
                    postconditions=[PostconditionResult(spec=_spec(severity="required"), status="verified", detail="")])
    bad = _envelope(capability="gmail_send", status="failed", execution_id="b")
    summary = build_verified_summary([ok, bad])
    assert summary.failed_capabilities == ["gmail_send"]


# ── renderers ────────────────────────────────────────────────────────────

def test_render_for_model_includes_instruction_and_failure_marker():
    summary = build_verified_summary([_envelope(status="failed", capability="gmail_send")])
    block = render_operation_status_for_model(summary)
    assert "gmail_send" in block
    assert "FAILED" in block
    assert "Describe ONLY what this list says happened" in block


def test_render_for_model_handles_no_operations():
    block = render_operation_status_for_model(build_verified_summary([]))
    assert "No tool executions recorded" in block


def test_render_for_user_has_no_model_instruction_language():
    summary = build_verified_summary([_envelope(status="failed", capability="gmail_send")])
    block = render_operation_status_for_user(summary)
    assert "gmail_send" in block
    assert "FAILED" in block
    assert "Describe ONLY" not in block
    assert block.startswith("[System-verified status]")


# ── audit_claims (secondary, observation-only) ──────────────────────────────

def test_audit_claims_silent_when_nothing_failed():
    summary = build_verified_summary([_envelope(status="success", postconditions=[
        PostconditionResult(spec=_spec(severity="required"), status="verified", detail=""),
    ])])
    assert audit_claims("Everything is done and successfully completed!", summary) == []


def test_audit_claims_fires_on_blanket_english_success_claim():
    ok = _envelope(capability="file_write", status="success", execution_id="a")
    bad = _envelope(capability="gmail_send", status="failed", execution_id="b")
    summary = build_verified_summary([ok, bad])
    violations = audit_claims("Everything is done -- both actions succeeded.", summary)
    assert violations
    assert "gmail_send" in violations[0]


def test_audit_claims_fires_on_blanket_turkish_success_claim():
    ok = _envelope(capability="file_write", status="success", execution_id="a")
    bad = _envelope(capability="gmail_send", status="failed", execution_id="b")
    summary = build_verified_summary([ok, bad])
    violations = audit_claims("Hepsi başarıyla tamamlandı.", summary)
    assert violations


def test_audit_claims_silent_when_response_correctly_hedges():
    ok = _envelope(capability="file_write", status="success", execution_id="a")
    bad = _envelope(capability="gmail_send", status="failed", execution_id="b")
    summary = build_verified_summary([ok, bad])
    text = "I successfully created the file, but sending the email failed."
    assert audit_claims(text, summary) == []


def test_audit_claims_silent_when_no_success_language_present():
    ok = _envelope(capability="file_write", status="success", execution_id="a")
    bad = _envelope(capability="gmail_send", status="failed", execution_id="b")
    summary = build_verified_summary([ok, bad])
    assert audit_claims("Here is a summary of the attempted operations.", summary) == []


def test_audit_claims_silent_on_empty_response():
    bad = _envelope(status="failed")
    summary = build_verified_summary([bad])
    assert audit_claims("", summary) == []
    assert audit_claims("   ", summary) == []
