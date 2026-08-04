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
