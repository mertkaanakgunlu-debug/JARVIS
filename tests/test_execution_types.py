"""Agent Runtime rev.2, Faz 1 -- the new jarvis/execution/ type shapes.

Pure data-shape tests: TaskContract/ExpectedOutcome (contract.py),
PostconditionSpec/PostconditionResult (postcondition.py) and
ExecutionEnvelope/build_shadow_envelope (envelope.py) are plain pydantic
models with no I/O, so these need no fixtures. What matters here is the
closed vocabulary (a typo'd Literal must fail loudly, not silently) and
build_shadow_envelope's honesty discipline: it must never claim more than
Faz 1 actually knows (see envelope.py's own docstring).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.execution.contract import ExpectedOutcome, TaskContract
from jarvis.execution.envelope import ExecutionEnvelope, build_shadow_envelope
from jarvis.execution.postcondition import PostconditionResult, PostconditionSpec


# ── contract.py ────────────────────────────────────────────────────────────

def test_task_contract_minimal_construction():
    c = TaskContract(task_id="t1", user_goal="draw a line chart of 1,4,9,16")
    assert c.required_capabilities == []
    assert c.expected_outcomes == []
    assert c.approval_scope is None


def test_expected_outcome_rejects_unknown_outcome_type():
    with pytest.raises(ValidationError):
        ExpectedOutcome(outcome_type="not_a_real_type", source="user_literal")


def test_expected_outcome_rejects_unknown_source():
    with pytest.raises(ValidationError):
        ExpectedOutcome(outcome_type="chart", source="not_a_real_source")


def test_task_contract_carries_typed_expected_outcomes():
    outcome = ExpectedOutcome(
        outcome_type="chart",
        params={"chart_type": "line", "expected_y": [1, 4, 9, 16]},
        source="user_literal",
    )
    c = TaskContract(
        task_id="t2", user_goal="draw 1,4,9,16",
        required_capabilities=["plot_data"], expected_outcomes=[outcome],
    )
    assert c.expected_outcomes[0].params["expected_y"] == [1, 4, 9, 16]


# ── postcondition.py ──────────────────────────────────────────────────────

def test_postcondition_spec_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        PostconditionSpec(kind="not_a_real_kind", source="task_contract")


def test_postcondition_spec_defaults_to_required_severity():
    spec = PostconditionSpec(kind="file_exists", source="tool_contract")
    assert spec.severity == "required"


def test_postcondition_result_rejects_unknown_status():
    spec = PostconditionSpec(kind="file_exists", source="policy")
    with pytest.raises(ValidationError):
        PostconditionResult(spec=spec, status="definitely_verified")


def test_postcondition_result_accepts_unverified_not_silently_verified():
    """The honesty discipline postcondition.py's docstring requires: absence
    of a runnable validator must show up as "unverified", a value that
    actually exists and round-trips -- not get coerced to "verified"."""
    spec = PostconditionSpec(kind="record_exists", source="policy")
    result = PostconditionResult(spec=spec, status="unverified", detail="no validator for this capability yet")
    assert result.status == "unverified"


# ── envelope.py ────────────────────────────────────────────────────────────

def test_execution_envelope_rejects_unknown_status():
    with pytest.raises(ValidationError):
        ExecutionEnvelope(
            execution_id="e1", capability="file_read", status="probably_fine",
            inputs_digest="abc",
        )


def test_execution_envelope_defaults_are_empty_not_fabricated():
    env = ExecutionEnvelope(
        execution_id="e1", capability="file_read", status="success",
        inputs_digest="abc",
    )
    assert env.artifacts == [] and env.postconditions == [] and env.validation == {}
    assert env.error_code is None


def test_build_shadow_envelope_success():
    env = build_shadow_envelope(
        tool_name="file_write", args={"path": "a.txt", "content": "hello"},
        ok=True, content="wrote 5 bytes", retryable=False, error_code=None,
        execution_id="call_1",
    )
    assert env.capability == "file_write"
    assert env.status == "success"
    assert env.execution_id == "call_1"
    assert env.retryable is False
    assert env.error_code is None
    # No raw argument value anywhere in the persisted envelope.
    dumped = str(env.model_dump())
    assert "hello" not in dumped


def test_build_shadow_envelope_failure_carries_error_code_and_retryable():
    env = build_shadow_envelope(
        tool_name="itu_mail", args={"action": "list_unread"}, ok=False,
        content="[TOOL_ERROR]\ntool=itu_mail\ncategory=dependency_missing\n"
                "message=imap-tools not installed\nretryable=true",
        retryable=True, error_code=None, execution_id="call_2",
    )
    assert env.status == "failed"
    assert env.retryable is True


def test_build_shadow_envelope_never_claims_postconditions_it_did_not_run():
    """Faz 1 has no postcondition runner (that's Faz 3) -- a shadow envelope
    must not claim verification it never performed."""
    env = build_shadow_envelope(
        tool_name="plot_data", args={"x": [1, 4, 9, 16], "y": [1, 4, 9, 16]},
        ok=True, content="saved chart.png", retryable=False, error_code=None,
        execution_id="call_3",
    )
    assert env.postconditions == []
    assert env.validation == {}


def test_build_shadow_envelope_same_call_same_digest():
    a = build_shadow_envelope(
        tool_name="file_read", args={"path": "a.txt"}, ok=True, content="x",
        retryable=False, error_code=None, execution_id="call_4",
    )
    b = build_shadow_envelope(
        tool_name="file_read", args={"path": "a.txt"}, ok=True, content="x",
        retryable=False, error_code=None, execution_id="call_5",
    )
    assert a.inputs_digest == b.inputs_digest  # same tool+args, different execution_id
