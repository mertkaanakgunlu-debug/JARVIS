"""Agent Runtime rev.2, Faz 2 -- jarvis.execution.approval's HMAC binding.

Central invariants under test: a signature verifies only for the EXACT
request it was minted for (any field change -- including args changing after
the fact, which is the real-world "repair invalidates approval" scenario --
breaks it), an expired approval is refused even with a perfect signature,
and a nonce can only ever be consumed once.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jarvis.execution import approval
from jarvis.execution.request import ExecutionRequest


def _req(**overrides) -> ExecutionRequest:
    base = dict(
        execution_id="call_0-abc123",
        capability="file_write",
        action="",
        normalized_args_digest="digest-a",
        target_resource="file_write:a.txt",
        risk_level=2,
        requires_confirmation=True,
        allowed=True,
        side_effect_type="local_write",
        created_at=datetime.now(timezone.utc).isoformat(),
        expiry=approval.new_expiry(300),
        single_use_nonce=approval.new_nonce(),
    )
    base.update(overrides)
    return ExecutionRequest(**base)


@pytest.fixture(autouse=True)
def _clean_nonce_state():
    """approval._consumed_nonces is process-local/module-level by design (see
    the module docstring) -- reset between tests so one test's consume()
    can't bleed into the next's fresh nonce (collision odds are astronomically
    low with token_hex(16), but the isolation should not depend on that)."""
    approval._consumed_nonces.clear()
    yield
    approval._consumed_nonces.clear()


# ── sign / verify happy path ──────────────────────────────────────────────

def test_verify_accepts_untampered_request():
    req = _req()
    sig = approval.sign(req)
    ok, reason = approval.verify(req, sig, current_args_digest=req.normalized_args_digest)
    assert ok is True and reason == "ok"


def test_sign_is_deterministic_for_the_same_request():
    req = _req()
    assert approval.sign(req) == approval.sign(req)


# ── tamper detection: any bound field changing breaks the signature ──────

@pytest.mark.parametrize("field,value", [
    ("execution_id", "call_0-DIFFERENT"),
    ("capability", "shell_run"),
    ("normalized_args_digest", "digest-b"),
    ("target_resource", "file_write:b.txt"),
    ("risk_level", 3),
    ("expiry", approval.new_expiry(9999)),
    ("single_use_nonce", "different-nonce"),
])
def test_verify_rejects_signature_after_any_bound_field_changes(field, value):
    req = _req()
    sig = approval.sign(req)
    tampered = req.model_copy(update={field: value})
    ok, reason = approval.verify(tampered, sig, current_args_digest=tampered.normalized_args_digest)
    assert ok is False
    assert reason == "signature_mismatch"


def test_verify_rejects_garbage_signature():
    req = _req()
    ok, reason = approval.verify(req, "not-a-real-signature", current_args_digest=req.normalized_args_digest)
    assert ok is False and reason == "signature_mismatch"


# ── the TOCTOU check: args changed between sign-time and execute-time ────

def test_verify_rejects_when_current_args_digest_differs_from_signed_one():
    """The repair scenario: the signature itself is still technically valid
    (nothing about the ExecutionRequest object changed), but the CURRENT
    tool_call's args no longer match what was signed -- e.g. a repaired call
    reusing an old approval. Must be refused, not silently allowed through
    because the signature checked out."""
    req = _req(normalized_args_digest="digest-a")
    sig = approval.sign(req)
    ok, reason = approval.verify(req, sig, current_args_digest="digest-DIFFERENT")
    assert ok is False and reason == "args_changed_since_approval"


# ── expiry ─────────────────────────────────────────────────────────────────

def test_verify_rejects_expired_approval():
    past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    req = _req(expiry=past)
    sig = approval.sign(req)
    ok, reason = approval.verify(req, sig, current_args_digest=req.normalized_args_digest)
    assert ok is False and reason == "approval_expired"


def test_verify_accepts_approval_well_within_ttl():
    req = _req(expiry=approval.new_expiry(300))
    sig = approval.sign(req)
    ok, _ = approval.verify(req, sig, current_args_digest=req.normalized_args_digest)
    assert ok is True


# ── single-use nonce ───────────────────────────────────────────────────────

def test_consume_then_verify_reports_already_used():
    req = _req()
    sig = approval.sign(req)
    approval.consume(req)
    ok, reason = approval.verify(req, sig, current_args_digest=req.normalized_args_digest)
    assert ok is False and reason == "approval_already_used"


def test_verify_does_not_itself_consume():
    """verify() is safe to call for inspection/logging without side effects
    -- only an explicit consume() call burns the nonce."""
    req = _req()
    sig = approval.sign(req)
    approval.verify(req, sig, current_args_digest=req.normalized_args_digest)
    ok, _ = approval.verify(req, sig, current_args_digest=req.normalized_args_digest)
    assert ok is True


def test_consume_is_idempotent():
    req = _req()
    approval.consume(req)
    approval.consume(req)  # must not raise
