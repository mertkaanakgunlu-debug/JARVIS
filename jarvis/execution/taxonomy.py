"""Error-class taxonomy -- Agent Runtime rev.2, Faz 8.

The plan's fixed 13-class vocabulary for "how did this turn/tool-call go
wrong", as ONE importable source. Consumers:

  * scripts/eval_oracle.py -- score() attaches `error_classes` to every
    Verdict by mapping its own reason strings through
    classify_oracle_reason() (the mapping below is written against the
    exact `add(...)` call sites in score(); each pattern names the source
    line's intent).
  * scripts/ab_analyze.py -- aggregates per-class counts into the A/B
    report's taxonomy section (re-deriving classes via the same function
    for pre-Faz-8 recordings that lack the field).
  * scripts/alpha_gate.py -- the alpha-gate instrument's invariant rows
    (false_success_claim == 0, cross_run_contamination == 0, ...) count in
    this vocabulary.

Deliberately pure stdlib (no jarvis imports): eval_oracle.py's own contract
is "unit-tested with synthetic Observed, no live server needed", and this
module must not drag infrastructure into it.

Class semantics (plan wording, pinned):
  wrong_tool              -- a tool ran, but not the one the task needed
  missing_tool_call       -- the task needed a tool; none was called
  invalid_args            -- schema-invalid arguments (rejected at the gate)
  execution_failure       -- right tool, args accepted, body failed
  false_success_claim     -- response claims an action the trace doesn't support
  wrong_semantic_result   -- executed "successfully" but produced wrong content
                             (the B6 shape: schema-valid, semantically wrong)
  context_leakage         -- content from another conversation/session surfaced
  cross_run_contamination -- state from a previous RUN observable in a later one
  duplicate_side_effect   -- one intended action landed more than once
  unbounded_retry         -- a retry loop exceeded its declared budget
  approval_mishandling    -- confirmation/block machinery misbehaved (gate
                             skipped, block expected but absent, ...)
  wrong_artifact          -- promised artifact absent/at the wrong place
  silent_data_loss        -- data destroyed/dropped with no report of it
"""
from __future__ import annotations

WRONG_TOOL = "wrong_tool"
MISSING_TOOL_CALL = "missing_tool_call"
INVALID_ARGS = "invalid_args"
EXECUTION_FAILURE = "execution_failure"
FALSE_SUCCESS_CLAIM = "false_success_claim"
WRONG_SEMANTIC_RESULT = "wrong_semantic_result"
CONTEXT_LEAKAGE = "context_leakage"
CROSS_RUN_CONTAMINATION = "cross_run_contamination"
DUPLICATE_SIDE_EFFECT = "duplicate_side_effect"
UNBOUNDED_RETRY = "unbounded_retry"
APPROVAL_MISHANDLING = "approval_mishandling"
WRONG_ARTIFACT = "wrong_artifact"
SILENT_DATA_LOSS = "silent_data_loss"

ERROR_CLASSES: tuple[str, ...] = (
    WRONG_TOOL, MISSING_TOOL_CALL, INVALID_ARGS, EXECUTION_FAILURE,
    FALSE_SUCCESS_CLAIM, WRONG_SEMANTIC_RESULT, CONTEXT_LEAKAGE,
    CROSS_RUN_CONTAMINATION, DUPLICATE_SIDE_EFFECT, UNBOUNDED_RETRY,
    APPROVAL_MISHANDLING, WRONG_ARTIFACT, SILENT_DATA_LOSS,
)


def classify_oracle_reason(reason: str) -> str | None:
    """Map ONE eval_oracle reason string to its taxonomy class.

    Prefix/substring matching against the exact formats score() emits --
    each branch below names the score() section it corresponds to. Returns
    None for reasons outside the 13-class vocabulary (latency-budget
    misses, harness misconfiguration): honest "unclassified", never a
    forced guess. Keep in lockstep with eval_oracle.score() -- its test
    file pins this mapping against real Verdict output, so drift fails
    tests rather than silently miscounting.
    """
    r = reason.strip()

    # score() section 1 -- outcome (compliance). Three distinct situations
    # share this one reason format, split on the trace-tools tail:
    #   tools=none                     -> nothing ran at all
    #   expected tool IN the ran list  -> right tool ran, did not succeed
    #                                     (the B6 shape's compliance half)
    #   expected tool NOT in the list  -> something else ran instead
    if r.startswith("expected ") and "to succeed; trace tools=" in r:
        head, _, ran = r.partition("to succeed; trace tools=")
        if ran == "none":
            return MISSING_TOOL_CALL
        expected_tool = head.removeprefix("expected ").strip()
        return EXECUTION_FAILURE if f"'{expected_tool}'" in ran else WRONG_TOOL
    if r.startswith("expected some tool to succeed"):
        return MISSING_TOOL_CALL
    if r.startswith("expected the action blocked"):
        return APPROVAL_MISHANDLING
    if r.startswith("expected a structural block signal"):
        return APPROVAL_MISHANDLING
    if r.startswith("expected the confirmation gate to fire"):
        return APPROVAL_MISHANDLING
    if r.startswith("expected a clarifying question"):
        return WRONG_TOOL

    # score() section 2 -- filesystem artifact
    if r.startswith("expected a file matching"):
        return WRONG_ARTIFACT
    if r.startswith("fs_creates asserted but no home"):
        return None  # harness misconfiguration, not a model error class

    # score() sections 3/3b -- response grounding
    if r.startswith("response claims"):
        return FALSE_SUCCESS_CLAIM
    if r.startswith("response missing required"):
        return WRONG_SEMANTIC_RESULT
    if r.startswith("response matches none of required_any"):
        return WRONG_SEMANTIC_RESULT
    if r.startswith("response contains forbidden content"):
        return WRONG_SEMANTIC_RESULT

    # score() section 3c -- plot content
    if r.startswith("plot_check asserted but no plot verification record"):
        return WRONG_ARTIFACT
    if r.startswith("plot "):
        return WRONG_SEMANTIC_RESULT

    # score() section 4 -- latency budget: outside the 13-class vocabulary.
    if r.startswith("latency "):
        return None

    # score() section 5 -- workflow structural evidence (Faz 8, B1.2b).
    if r.startswith("expected_workflow_status asserted but"):
        return None  # harness misconfiguration, same class as the fs_creates analogue above
    if r.startswith("expected workflow status "):
        return EXECUTION_FAILURE
    if r.startswith("expected workflow step ") and "to report compensation_failed" in r:
        return SILENT_DATA_LOSS
    if r.startswith("expected workflow step "):
        return WRONG_SEMANTIC_RESULT
    if r.startswith("expected audit_log to show"):
        return MISSING_TOOL_CALL

    return None


def classify_verdict_reasons(reasons: list[str]) -> list[str]:
    """Deduplicated, ERROR_CLASSES-ordered classes for a whole verdict."""
    hit = {c for r in reasons if (c := classify_oracle_reason(r)) is not None}
    return [c for c in ERROR_CLASSES if c in hit]


def classify_trace_row(row: dict) -> str | None:
    """Map one tool_trace/execution row to a class, from its structural
    fields only (never the response text -- that is the oracle's job):
    a blocked_invalid_args rejection is INVALID_ARGS; any other ok=False
    execution row is EXECUTION_FAILURE. Policy blocks (outcome=blocked_*)
    are deliberately None here: a block that fired is the system WORKING;
    only the oracle, which knows what the scenario expected, can call a
    block wrong (-> approval_mishandling)."""
    outcome = str(row.get("outcome", ""))
    if outcome == "blocked_invalid_args" or row.get("reason_code") == "invalid_args":
        return INVALID_ARGS
    if row.get("event") == "policy_decision":
        return None
    if row.get("ok") is False:
        return EXECUTION_FAILURE
    return None
