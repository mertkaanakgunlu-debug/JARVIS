"""HMAC approval binding -- Agent Runtime rev.2, Faz 2 (reviewer #2/#6).

Before this module, confirmation_node's interrupt/resume round-trip approved
whatever tool_call happened to be sitting in graph state at resume time --
nothing tied the user's "yes" to the SPECIFIC arguments they were shown. This
closes that gap: prepare_execution (nodes.py) mints an ExecutionRequest and
signs it with sign() before the interrupt; confirmation_node re-derives the
current call's digest and calls verify() right before handing off to the
"tools" node, so a repair that changed the arguments between "shown to user"
and "about to execute" invalidates the old approval instead of silently
running with new args under an old yes.

Process-local key (reviewer's own call, plan section C): "tek kullanicili
yerel asistan icin yeterli, sertifika altyapisi gereksiz" -- a certificate
chain is the wrong tool for a single-user local assistant. Concrete
consequence, stated plainly rather than glossed over: a process restart
invalidates every approval still awaiting an answer (the key that signed it
is gone) -- verify() reports that as a plain signature mismatch, and
confirmation_node's caller treats it exactly like a stale/tampered approval
(deny, ask the model to re-issue), not a crash. Given this is a local,
single-operator assistant restarting the same process a pending approval was
issued from, "please re-approve" is an acceptable failure mode for the
edge case where the process happens to restart in the few minutes a prompt
sits unanswered.

Single-use nonces are tracked in memory only (same process-local scope as
the key itself -- both reset together on restart, so there's no separate
persistence story that could drift from the key's own lifetime).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
from datetime import datetime, timedelta, timezone

from jarvis.execution.request import ExecutionRequest

_PROCESS_KEY = secrets.token_bytes(32)

_lock = threading.Lock()
_consumed_nonces: set[str] = set()


def new_nonce() -> str:
    return secrets.token_hex(16)


def new_expiry(ttl_seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)).isoformat()


def _payload(req: ExecutionRequest) -> bytes:
    # Exactly the field list the plan names: "execution_id * capability *
    # normalized_args_digest * target_resource * risk_level * expiry *
    # single_use_nonce". "|" is a safe separator -- every field is either a
    # digest/id (hex/uuid-shaped) or an int, none of which can contain "|".
    parts = [
        req.execution_id, req.capability, req.normalized_args_digest,
        req.target_resource, str(req.risk_level), req.expiry, req.single_use_nonce,
    ]
    return "|".join(parts).encode("utf-8")


def sign(req: ExecutionRequest) -> str:
    return hmac.new(_PROCESS_KEY, _payload(req), hashlib.sha256).hexdigest()


def verify(req: ExecutionRequest, signature: str, *, current_args_digest: str) -> tuple[bool, str]:
    """(ok, reason). First failing check wins -- signature, then whether the
    arguments actually being executed still match what was signed (the TOCTOU
    check), then expiry, then single-use. Does NOT consume the nonce -- call
    consume() separately once the caller has decided to actually proceed, so
    a verify-only inspection (e.g. logging) can't accidentally burn it."""
    expected = sign(req)
    if not hmac.compare_digest(expected, signature):
        return False, "signature_mismatch"
    if not hmac.compare_digest(current_args_digest, req.normalized_args_digest):
        return False, "args_changed_since_approval"
    try:
        expiry_dt = datetime.fromisoformat(req.expiry)
    except ValueError:
        return False, "invalid_expiry"
    if datetime.now(timezone.utc) > expiry_dt:
        return False, "approval_expired"
    with _lock:
        if req.single_use_nonce in _consumed_nonces:
            return False, "approval_already_used"
    return True, "ok"


def consume(req: ExecutionRequest) -> None:
    """Mark this approval's nonce spent. Idempotent: consuming an
    already-consumed nonce is a no-op, not an error -- callers only ever
    consume after a successful verify() in the same call, so a double-consume
    would only happen via a genuine replay, which verify() has already
    caught and refused before consume() would ever be reached for it."""
    with _lock:
        _consumed_nonces.add(req.single_use_nonce)
