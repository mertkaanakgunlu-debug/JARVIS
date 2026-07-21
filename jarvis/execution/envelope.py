"""ExecutionEnvelope -- Agent Runtime rev.2, Faz 1 shadow ledger.

The one normalized record of "what actually happened" for a single tool
call. Faz 1 wires this in as a pure OBSERVER: jarvis/graph/tool_accounting.py's
tool_result_accounting node builds one per call via build_shadow_envelope()
below when Settings.execution_contract_mode != "off" -- nothing reads an
envelope back to change a decision yet. Faz 2 is where an ExecutionEnvelope
first influences control flow (idempotency journal lookups); Faz 4 is where
compose_node first reads it instead of raw ToolMessages.

Never carries raw tool arguments or raw tool output -- inputs_digest and
normalized_output both go through jarvis.execution.redaction. This is the
concrete fix for the gap the plan's root-cause section documents:
audit_log.record's args_preview/result_preview and
tool_execution_ledger's content_head both wrote raw, unredacted text to
disk (the latter through the LangGraph SqliteSaver checkpointer, not just
an append-only log).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from jarvis.execution.postcondition import PostconditionResult

ExecutionStatus = Literal[
    "success", "failed", "blocked", "partial", "invalid_args", "timed_out"
]


class ExecutionEnvelope(BaseModel):
    execution_id: str
    capability: str
    status: ExecutionStatus
    inputs_digest: str
    normalized_output: str | None = None
    artifacts: list[str] = []
    side_effects: list[str] = []
    evidence: list[str] = []
    error_code: str | None = None
    retryable: bool = False
    postconditions: list[PostconditionResult] = []
    validation: dict[str, Any] = {}
    timed_out: bool = False
    execution_may_still_be_running: bool = False
    worker_terminated: bool = False
    created_at: str = ""  # datetime.now().isoformat() -- see jarvis.audit_log's same convention


def build_shadow_envelope(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    ok: bool,
    content: str,
    retryable: bool,
    error_code: str | None,
    execution_id: str,
    timed_out: bool = False,
    execution_may_still_be_running: bool = False,
    worker_terminated: bool = False,
    postconditions: list | None = None,
) -> ExecutionEnvelope:
    """Build one envelope from the same per-call data
    tool_result_accounting already computes (name, args, ok, content,
    retryable, blocked-code). execution_id reuses the model's own
    tool_call_id -- available today with no new ID scheme; Agent Runtime
    rev.2, Faz 2's prepare_execution node mints its own, separate,
    per-attempt id for approval binding (jarvis.execution.request) that is
    deliberately NOT this one -- the two ids serve different purposes and
    are never meant to be interchangeable. artifacts/side_effects/evidence
    stay empty -- nothing in this repo populates them yet.

    Faz 3: status now becomes "timed_out" (never "success"/"failed") when
    timed_out=True -- timed_out takes precedence over ok, since a call that
    timed out is neither a clean success nor a clean, ended failure.
    execution_may_still_be_running/worker_terminated ride straight through
    from tool_result_accounting's parse of the [TOOL_ERROR] block's own
    fields (see safe_tools.format_tool_error) -- this function does not
    re-derive them. postconditions defaults to [] (Faz 1's original
    honesty note still applies: no caller, no verification claimed) but a
    caller with real PostconditionResults (jarvis.execution.
    postcondition_runner, Faz 3) can now pass them through.
    """
    from jarvis.execution.redaction import digest_args, redact_preview

    if timed_out:
        status: ExecutionStatus = "timed_out"
    else:
        status = "success" if ok else "failed"

    return ExecutionEnvelope(
        execution_id=execution_id,
        capability=tool_name,
        status=status,
        inputs_digest=digest_args(tool_name, args),
        normalized_output=redact_preview(content),
        error_code=error_code,
        retryable=retryable,
        timed_out=timed_out,
        execution_may_still_be_running=execution_may_still_be_running,
        worker_terminated=worker_terminated,
        postconditions=postconditions or [],
        created_at=datetime.now().isoformat(),
    )
