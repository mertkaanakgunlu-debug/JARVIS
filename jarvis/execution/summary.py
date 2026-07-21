"""VerifiedExecutionSummary -- Agent Runtime rev.2, Faz 4.

compose_node (jarvis/graph/nodes.py) used to hand the bare composer LLM raw
ToolMessage content and trust it to correctly describe what happened -- the
B6 hallucination (a tool that FAILED, narrated by the model as a success)
and the partial-success gap the plan names explicitly ("2 tool'dan 1'i
basarili, model ikisini de iddia ediyor" -- passes today, uncaught) both
live in that trust gap. This module turns a turn's ExecutionEnvelope list
into one deterministic, CODE-authored verdict per operation -- the plan's
own words: the "operation status" block is embedded in code, not left to
the model.

Two renderers, two different audiences:
  render_operation_status_for_model -- fed to the composer LLM as ground
    truth (enforce mode only), replacing raw ToolMessages in that call's
    invocation so the model can no longer independently narrate a verdict.
  render_operation_status_for_user -- appended UNCONDITIONALLY by
    compose_node to the final response whenever any_failed is true. THIS is
    the primary safety net behind the plan's acceptance test ("an unverified
    operation claim does not reach the user") -- it is a structural
    guarantee (fires on the envelope data alone), not a claim-text-detection
    gate, so it cannot be defeated by a phrasing the detector below doesn't
    recognize.

audit_claims() is a SECONDARY, observation-only signal (the plan: "claim
extractor stays but becomes secondary -- not the primary safety boundary,
but audit and observation"). compose_node logs its findings for future
calibration; nothing gates on it.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from jarvis.execution.envelope import ExecutionEnvelope
from jarvis.execution.postcondition import PostconditionResult

DisplayStatus = Literal[
    "confirmed", "reported_success_unverified",
    "reported_success_verification_failed", "failed", "partial",
]

# Envelope-reported outcomes that are never a clean success, regardless of
# postconditions -- a tool that says it failed/was blocked/timed out/had
# invalid args IS a failure; independent verification cannot upgrade that.
_HARD_FAILURE_TOOL_STATUSES = {"failed", "blocked", "invalid_args", "timed_out"}

# display_status values a downstream reader should treat as "did not cleanly
# succeed" -- drives VerifiedExecutionSummary.any_failed, the structural
# trigger for render_operation_status_for_user's unconditional append.
_FAILURE_DISPLAY_STATUSES = {"failed", "partial", "reported_success_verification_failed"}

_STATUS_LABELS: dict[str, str] = {
    "confirmed": "completed and independently verified",
    "reported_success_unverified": "reported successful by the tool (not independently verified)",
    "reported_success_verification_failed": (
        "reported successful by the tool, but independent verification FAILED "
        "-- do not treat this as a success"
    ),
    "failed": "FAILED -- did not complete",
    "partial": "PARTIALLY completed",
}


class VerifiedOperation(BaseModel):
    execution_id: str
    capability: str
    tool_status: str  # ExecutionEnvelope.status, straight through
    postcondition_verdict: Literal["verified", "failed", "unverified", "not_applicable"]
    display_status: DisplayStatus
    detail: str = ""  # redacted, truncated -- ExecutionEnvelope.normalized_output


class VerifiedExecutionSummary(BaseModel):
    operations: list[VerifiedOperation] = []

    @property
    def any_failed(self) -> bool:
        return any(op.display_status in _FAILURE_DISPLAY_STATUSES for op in self.operations)

    @property
    def any_unverified(self) -> bool:
        return any(op.display_status == "reported_success_unverified" for op in self.operations)

    @property
    def all_confirmed(self) -> bool:
        return bool(self.operations) and all(op.display_status == "confirmed" for op in self.operations)

    @property
    def failed_operations(self) -> list[VerifiedOperation]:
        return [op for op in self.operations if op.display_status in _FAILURE_DISPLAY_STATUSES]

    @property
    def failed_capabilities(self) -> list[str]:
        return [op.capability for op in self.failed_operations]


def _postcondition_verdict(
    postconditions: list[PostconditionResult],
) -> Literal["verified", "failed", "unverified", "not_applicable"]:
    """Aggregate a call's postcondition results into one verdict.

    Only "required" severity results decide this -- a "warning"-severity
    postcondition is informational and must never turn an otherwise-clean
    success into a reported failure. No required postconditions declared at
    all -- most of the 36 tools today, e.g. web_search -- is honestly
    "not_applicable", never silently "verified" (postcondition.py's own
    honesty discipline, restated here).
    """
    required = [p for p in postconditions if p.spec.severity == "required"]
    if not required:
        return "not_applicable"
    if any(p.status == "failed" for p in required):
        return "failed"
    if all(p.status == "verified" for p in required):
        return "verified"
    return "unverified"


def _display_status(tool_status: str, postcondition_verdict: str) -> DisplayStatus:
    if tool_status in _HARD_FAILURE_TOOL_STATUSES:
        return "failed"
    if tool_status == "partial":
        return "partial"
    # Only "success" remains of ExecutionEnvelope's 6-value status Literal.
    if postcondition_verdict == "failed":
        return "reported_success_verification_failed"
    if postcondition_verdict == "verified":
        return "confirmed"
    return "reported_success_unverified"


def build_verified_summary(envelopes: list[dict]) -> VerifiedExecutionSummary:
    """Build the turn's ground-truth operation list from raw ExecutionEnvelope
    dicts (state["execution_envelopes"], .model_dump()'d by
    tool_result_accounting). Never raises on a malformed entry -- same
    "auxiliary observation can't break the turn" discipline as
    postcondition_runner.run_postconditions(); one bad envelope is dropped,
    not fatal to the rest.
    """
    operations: list[VerifiedOperation] = []
    for raw in envelopes:
        try:
            env = ExecutionEnvelope(**raw)
        except Exception:
            continue
        pc_verdict = _postcondition_verdict(env.postconditions)
        operations.append(VerifiedOperation(
            execution_id=env.execution_id,
            capability=env.capability,
            tool_status=env.status,
            postcondition_verdict=pc_verdict,
            display_status=_display_status(env.status, pc_verdict),
            detail=env.normalized_output or "",
        ))
    return VerifiedExecutionSummary(operations=operations)


def _operation_line(op: VerifiedOperation) -> str:
    label = _STATUS_LABELS[op.display_status]
    detail = f" -- {op.detail}" if op.detail else ""
    return f"- {op.capability}: {label}{detail}"


def render_operation_status_for_model(summary: VerifiedExecutionSummary) -> str:
    """Ground-truth block fed to the composer LLM in enforce mode -- see this
    module's docstring. compose_node drops raw ToolMessages from the same
    invocation when it uses this, so it is the model's ONLY source for "what
    happened" in that call."""
    body = (
        "\n".join(_operation_line(op) for op in summary.operations)
        if summary.operations else "No tool executions recorded for this turn."
    )
    return (
        "[Verified operation status -- computed by the system, not the model]\n"
        f"{body}\n"
        "Describe ONLY what this list says happened. Never claim success for "
        "anything marked FAILED, PARTIALLY completed, or 'verification FAILED'. "
        "For anything 'not independently verified', say it was attempted or "
        "reported, not confirmed."
    )


def render_operation_status_for_user(summary: VerifiedExecutionSummary) -> str:
    """The same facts, phrased for the end user. See this module's docstring
    for why this -- not claim-text detection -- is the actual safety net."""
    body = "\n".join(_operation_line(op) for op in summary.operations)
    return f"[System-verified status]\n{body}"


# ── Secondary, observation-only claim audit ─────────────────────────────────

_SUCCESS_PATTERNS = [re.compile(p, re.I) for p in (
    r"\ball (?:done|set|good|finished|completed)\b",
    r"\bboth (?:succeeded|worked|are done|completed|finished)\b",
    r"\beverything (?:is done|worked|succeeded|is set|completed)\b",
    r"\bsuccessfully (?:completed|finished|created|sent|ran|executed|added|deleted|scheduled)\b",
    r"\bhepsi(?:ni)? (?:tamam|başar[ıi]yla|bitti|hallettim)\b",
    r"\bikisi(?:ni)? de (?:tamamla|başar|bitir|hallettim|yaptım)\w*\b",
    r"\bbaşar[ıi]yla tamamla\w*\b",
)]

_NEGATION_MARKERS = [re.compile(p, re.I) for p in (
    r"\bfail\w*\b", r"\berror\b", r"\bcould ?n[o']?t\b", r"\bunable\b",
    r"\bdid ?n[o']?t\b", r"\bwas not\b", r"\bweren't\b",
    r"başar[ıi]s[ıi]z", r"\bhata\b", r"\byapılamad[ıi]\b",
    r"olu[şs]turulamad[ıi]", r"g[oö]nderilemed[ıi]", r"\bolmad[ıi]\b",
)]


def audit_claims(response_text: str, summary: VerifiedExecutionSummary) -> list[str]:
    """Secondary, observation-only signal -- see this module's docstring.

    Deliberately conservative, NOT full NLU: fires only when the response
    contains a strong, UNQUALIFIED blanket-success phrase, at least one
    operation genuinely did not succeed, and the response contains no
    negation/failure language anywhere (a model that correctly hedges --
    "the file was created but the email failed" -- must not be flagged).
    False negatives are expected and accepted; nothing reads this return
    value to gate behavior -- render_operation_status_for_user's
    unconditional append is the actual enforcement mechanism.
    """
    if not summary.any_failed or not response_text or not response_text.strip():
        return []
    if not any(p.search(response_text) for p in _SUCCESS_PATTERNS):
        return []
    if any(p.search(response_text) for p in _NEGATION_MARKERS):
        return []
    # Tie the claim to the specific execution_id(s) that contradict it, not
    # just the tool name -- the same capability can legitimately appear more
    # than once in one turn (a retried call, two different files written).
    failing = ", ".join(f"{op.capability} ({op.execution_id})" for op in summary.failed_operations)
    return [f"response makes an unqualified success claim while {failing} did not complete successfully"]
