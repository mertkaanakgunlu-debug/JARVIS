"""Counting helpers for the completion-contract gate, kept where pytest can see them.

`scripts/completion_contract_ab.py` sets JARVIS_HOME, chdir()s into a scratch
home and imports the agent at module scope, so a test cannot import it. These
two functions decide every number the gate prints, so they live here instead --
the same split `revision_scoring.py` has from `revision_gate.py`.

`rate()` exists in this form because of a real defect found by the first smoke
run: `source_mutation` is a dict (`{"changed", "deleted", "any"}`), which is
always truthy, so a naive `if row.get(key)` reported n/n mutations on every arm
while the gate clause -- reading `.get("any")` -- correctly reported zero. A
safety counter that is always full is a safety counter nobody reads.
"""

from __future__ import annotations

from collections.abc import Iterable


def rate(rows: list[dict], key: str) -> tuple[int, int]:
    """(hits, total). A dict-valued field counts by its `any` flag, not by
    being a dict."""
    def truthy(row: dict) -> bool:
        value = row.get(key)
        return bool(value.get("any")) if isinstance(value, dict) else bool(value)

    return sum(1 for r in rows if truthy(r)), len(rows)


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile: the smallest value at or above rank ceil(q*n).

    Textbook nearest-rank, not an interpolated index. The first version used
    `round(q * (n - 1))` and Python's banker's rounding made p50 of
    [1, 2, 3, 4] come out as 3.0 -- above the median, on the metric the gate
    compares against a hard 60-second ceiling. A latency percentile that
    rounds the wrong way is a gate clause that fails runs it should pass.

    Empty input is 0.0, never an exception: a clause with no data must read
    UNMEASURED, not take down the run that produced every other number.
    """
    if not values:
        return 0.0
    import math

    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return round(ordered[min(rank, len(ordered)) - 1], 2)


def honest_failure_retry_evidence(
    *,
    ledger: Iterable[dict] | None,
    repair_boundaries: Iterable[dict] | None,
    capabilities: Iterable[str],
    invalid_args_history: Iterable[dict] | None = None,
    preexecution_history: Iterable[dict] | None = None,
) -> list[dict]:
    """Return call-level evidence of an honest failure retried after repair.

    This is the corrected *diagnostic* for completion-contract Finding 2. The
    historical pre-registered gate intentionally keeps its original final-row
    predicate. Each completion-repair boundary records the call IDs that had
    completed before that repair. The append-only execution ledger then proves
    both sides of the temporal relation: a failed execution before the boundary
    and a distinct, later execution of the same capability after it.

    IDs in invalid-argument or pre-execution histories are excluded even if a
    malformed fixture supplies a ledger-like row for them; production never
    writes a ledger row for calls that did not execute. Unknown outcomes are
    also not honest failures because execution may still be in flight.
    """
    rows = [row for row in (ledger or ()) if isinstance(row, dict)]
    relevant = {str(name) for name in capabilities if str(name)}

    def _history_ids(history: Iterable[dict] | None) -> set[str]:
        return {
            str(row.get("tool_call_id") or "")
            for row in (history or ())
            if isinstance(row, dict) and row.get("tool_call_id")
        }

    excluded_ids = _history_ids(invalid_args_history) | _history_ids(preexecution_history)
    evidence: list[dict] = []
    diagnosed_failures: set[str] = set()

    for repair_index, boundary in enumerate(repair_boundaries or (), start=1):
        if not isinstance(boundary, dict):
            continue
        before_repair = {
            str(call_id) for call_id in boundary.get("after_tool_call_ids", ()) if call_id
        }
        if not before_repair:
            # MISSING_NO_ATTEMPT: the completion repair had no prior call to
            # classify as an honest execution failure.
            continue

        for failed_index, failed in enumerate(rows):
            failed_id = str(failed.get("tool_call_id") or "")
            tool = str(failed.get("tool") or "")
            if (
                not failed_id
                or failed_id in diagnosed_failures
                or failed_id not in before_repair
                or failed_id in excluded_ids
                or tool not in relevant
                or failed.get("ok") is not False
                or str(failed.get("outcome") or "") == "unknown"
            ):
                continue

            retry = next((
                candidate for candidate in rows[failed_index + 1:]
                if str(candidate.get("tool_call_id") or "")
                and str(candidate.get("tool_call_id") or "") != failed_id
                and str(candidate.get("tool_call_id") or "") not in before_repair
                and str(candidate.get("tool_call_id") or "") not in excluded_ids
                and str(candidate.get("tool") or "") == tool
            ), None)
            if retry is None:
                continue

            evidence.append({
                "tool": tool,
                "failed_tool_call_id": failed_id,
                "retry_tool_call_id": str(retry.get("tool_call_id") or ""),
                "retry_ok": retry.get("ok") is True,
                "repair_index": repair_index,
            })
            diagnosed_failures.add(failed_id)

    return evidence
