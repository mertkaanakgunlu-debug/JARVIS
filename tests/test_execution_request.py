"""Agent Runtime rev.2, Faz 2 -- jarvis.execution.request's ExecutionRequest.

Pure data-shape tests, same style as test_execution_types.py: a plain
pydantic model, frozen, with a closed vocabulary on idempotency/
task_contract_status. What matters here is that it really is immutable
(prepare_execution mints a new one rather than patching an old one -- this
is the guarantee that makes "repair invalidates the old approval" true) and
that it round-trips through model_dump()/reconstruction the way graph state
needs (state["execution_requests"] stores dicts, not live objects).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.execution.request import ExecutionRequest


def _kwargs(**overrides) -> dict:
    base = dict(
        execution_id="call_0-abc123",
        capability="file_write",
        action="",
        normalized_args_digest="deadbeef",
        target_resource="file_write:a.txt",
        risk_level=2,
        requires_confirmation=False,
        allowed=True,
        side_effect_type="local_write",
        created_at="2026-07-21T00:00:00+00:00",
        expiry="2026-07-21T00:05:00+00:00",
        single_use_nonce="nonce123",
    )
    base.update(overrides)
    return base


def test_minimal_construction_defaults():
    req = ExecutionRequest(**_kwargs())
    assert req.idempotency == "none"
    assert req.task_contract_status == "no_contract"


def test_is_frozen_immutable():
    req = ExecutionRequest(**_kwargs())
    with pytest.raises(ValidationError):
        req.risk_level = 3


def test_rejects_unknown_idempotency_value():
    with pytest.raises(ValidationError):
        ExecutionRequest(**_kwargs(idempotency="sometimes"))


def test_rejects_unknown_task_contract_status():
    with pytest.raises(ValidationError):
        ExecutionRequest(**_kwargs(task_contract_status="verified"))


def test_schema_has_no_raw_argument_field():
    """The whole point of normalized_args_digest -- this object is signed and
    threaded through checkpointed graph state, so the schema itself must
    expose no field that could hold a raw argument value, only its digest.
    A field named e.g. "args" or "normalized_args" slipping in later would
    reopen exactly the gap redaction.py/envelope.py exist to close."""
    fields = set(ExecutionRequest.model_fields)
    assert not any(f in ("args", "raw_args", "normalized_args") for f in fields), fields


def test_round_trips_through_model_dump():
    req = ExecutionRequest(**_kwargs())
    dumped = req.model_dump()
    rebuilt = ExecutionRequest(**dumped)
    assert rebuilt == req
