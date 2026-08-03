"""Did the chain step actually DEMONSTRATE anything? (Post-MVP Faz 4 gate)

This module exists because of a measurement bug that survived a full gate run
and made it into a handoff document as evidence.

`scripts/revision_gate.py` runs all seven steps on every chain and scores each
one by comparing the stored spec before and after the turn. When the model
never draws a chart, that comparison is `{} == {}`:

    _untouched({}, {})  ->  {} != {} is False  ->  PASS
    _undone({}, {})     ->  kind is not "bar", every carried key "" == ""  -> PASS

So three chains that never produced an object reported the "must not touch the
chart" and "undo" steps as successes. The run showed `undo 5/5` while only two
chains had ever held a chart. Nothing was wrong with the checkers -- they answer
"is the post-state right?", and against an empty state the answer genuinely is
"nothing moved". The missing question is the one asked here: **was this step in
a position to demonstrate anything at all?**

Three-valued on purpose. `False` means "observed and failed"; `None` means "never
observed". Summing those into one numerator is precisely the defect above.

The checkers stay in the script and are NOT modified. They are pure post-state
predicates and must not learn about eligibility -- merging the two would create
a second behaviour table to drift against. This module combines their raw output
with a precondition read from the state BEFORE the turn ran.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

# Step indices, mirroring scripts/revision_gate.py's CHAIN.
STEP_CREATE = 0
STEP_COLOUR = 1
STEP_TITLE = 2
STEP_KIND = 3
STEP_UNRELATED = 4
STEP_THANKS = 5
STEP_UNDO = 6

# The two turns whose claim is "no chart tool ran AND nothing moved". For these,
# an unchanged spec alone is not success: a model that called chart_revise and
# happened to leave the spec identical still violated the rule.
MUST_NOT_TOUCH_STEPS = frozenset({STEP_UNRELATED, STEP_THANKS})

# Steps whose eligibility depends on run-time state. Step 0 is always eligible,
# so it is the one step that can be judged over every run unconditionally.
CONDITIONAL_STEPS = (
    STEP_COLOUR, STEP_TITLE, STEP_KIND, STEP_UNRELATED, STEP_THANKS, STEP_UNDO,
)

# Below this many eligible observations a conditional step's rate is reported
# but never counted as a pass -- 2/2 is not evidence, it is a small sample.
MIN_ELIGIBLE = 3

# The targets the CHAIN's checkers look for, kept in the SAME form the checkers
# use so the two cannot drift. _is_red matches a set of spellings; _titled looks
# for the substring "2026", not an exact title; _is_bar compares kind.
_RED_SPELLINGS = frozenset({"red", "kırmızı", "kirmizi"})
_TITLE_MARKER = "2026"
_BAR = "bar"


@dataclass(frozen=True)
class StepScore:
    """One step's verdict, with the raw checker output preserved.

    `raw_*` is kept even when the step is ineligible: it is what makes the
    diagnostic "raw-check" column possible, and dropping it would hide the very
    vacuous passes this module exists to expose.
    """

    eligible: bool
    skip_reason: str
    raw_spec_ok: bool
    raw_tool_ok: bool
    raw_why: str
    spec_ok: bool | None
    tool_ok: bool | None
    claim_ok: bool | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible, "skip_reason": self.skip_reason,
            "raw_spec_ok": self.raw_spec_ok, "raw_tool_ok": self.raw_tool_ok,
            "raw_why": self.raw_why, "spec_ok": self.spec_ok,
            "tool_ok": self.tool_ok, "claim_ok": self.claim_ok,
        }


def _spec_of(pre_obj) -> dict[str, Any]:
    return dict(getattr(pre_obj, "spec", None) or {})


def _has_bar_transition(pre_obj) -> bool:
    """Is there a real `kind -> bar` change in this object's history?

    A non-empty history is not enough. If the model happened to draw a bar chart
    at turn 0, the "sütun grafiği olsun" turn changed nothing, and the history
    holds only the colour and title revisions -- so `undo` has no kind change to
    take back and the step cannot demonstrate the behaviour being claimed.

    Deliberately does NOT require the LAST revision to be the kind change. If an
    unrelated turn wrongly appended a revision, undo will take back the wrong one
    and `_undone()` must catch that as a FAILURE -- hiding it behind N/A would
    turn a real bug into a skipped row.
    """
    for revision in getattr(pre_obj, "revision_history", ()) or ():
        changes = dict(getattr(revision, "changes", None) or {})
        before = dict(getattr(revision, "before", None) or {})
        if (str(changes.get("kind", "")).casefold() == _BAR
                and str(before.get("kind", "")).casefold() != _BAR):
            return True
    return False


def eligibility_for(step: int, pre_obj) -> tuple[bool, str]:
    """Could this step have demonstrated its claim, given the state before it?

    Two independent reasons a step cannot: there is no chart to act on, or the
    requested change was ALREADY true. The second matters more than it looks --
    turn 0 does not constrain the chart type (the harness deliberately scores
    only "an object exists", because a live run once drew a scatter and that is
    a defensible reading of the request). So a model that opens with a bar chart
    makes "sütun grafiği olsun" pass without calling anything.
    """
    if step == STEP_CREATE:
        return True, ""

    if pre_obj is None:
        return False, "no active chart"

    spec = _spec_of(pre_obj)

    if step == STEP_COLOUR:
        if str(spec.get("color", "")).casefold() in _RED_SPELLINGS:
            return False, "target already red"
        return True, ""

    if step == STEP_TITLE:
        if _TITLE_MARKER in str(spec.get("title", "")):
            return False, "target title already set"
        return True, ""

    if step == STEP_KIND:
        if str(spec.get("kind", "")).casefold() == _BAR:
            return False, "target already bar"
        return True, ""

    if step in MUST_NOT_TOUCH_STEPS:
        return True, ""

    if step == STEP_UNDO:
        if str(spec.get("kind", "")).casefold() != _BAR:
            return False, "no bar state to undo"
        if not (getattr(pre_obj, "revision_history", ()) or ()):
            return False, "nothing to undo"
        if not _has_bar_transition(pre_obj):
            return False, "no bar revision to undo"
        return True, ""

    return True, ""


def score_step(
    *,
    step: int,
    pre_obj,
    raw_spec_ok: bool,
    raw_tool_ok: bool,
    raw_why: str = "",
) -> StepScore:
    """Raw checker output + precondition -> the verdict the report counts."""
    eligible, skip_reason = eligibility_for(step, pre_obj)
    raw_spec_ok, raw_tool_ok = bool(raw_spec_ok), bool(raw_tool_ok)

    if not eligible:
        return StepScore(
            eligible=False, skip_reason=skip_reason,
            raw_spec_ok=raw_spec_ok, raw_tool_ok=raw_tool_ok, raw_why=raw_why,
            spec_ok=None, tool_ok=None, claim_ok=None,
        )

    # A "must not touch" turn claims TWO things at once, and an unchanged spec
    # proves only one of them.
    claim_ok = (raw_spec_ok and raw_tool_ok) if step in MUST_NOT_TOUCH_STEPS else raw_spec_ok
    return StepScore(
        eligible=True, skip_reason="",
        raw_spec_ok=raw_spec_ok, raw_tool_ok=raw_tool_ok, raw_why=raw_why,
        spec_ok=raw_spec_ok, tool_ok=raw_tool_ok, claim_ok=claim_ok,
    )


# ── Aggregation ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StepSummary:
    step: int
    total: int            # every run of this step
    demonstrated: int     # claim_ok is True
    eligible_n: int
    eligible_pass: int
    na: int
    raw_pass: int         # diagnostic only -- never a success metric
    tool_pass: int        # among eligible rows

    @property
    def sufficient(self) -> bool:
        return self.eligible_n >= MIN_ELIGIBLE

    @property
    def passed(self) -> bool:
        """Every eligible observation demonstrated the claim."""
        return self.eligible_n > 0 and self.eligible_pass == self.eligible_n


@dataclass(frozen=True)
class GateReport:
    steps: list[StepSummary]
    full_chain_unconditional: tuple[int, int]
    full_chain_conditional: tuple[int, int]
    outcome_status: str      # PASS | FAIL
    coverage_status: str     # SUFFICIENT | INSUFFICIENT
    gate_pass: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_status": self.outcome_status,
            "coverage_status": self.coverage_status,
            "gate_pass": self.gate_pass,
            "full_chain_unconditional": list(self.full_chain_unconditional),
            "full_chain_conditional": list(self.full_chain_conditional),
            "steps": [vars(s) for s in self.steps],
        }


def summarize_step(step: int, rows: Sequence[dict]) -> StepSummary:
    eligible = [r for r in rows if r.get("eligible")]
    return StepSummary(
        step=step,
        total=len(rows),
        demonstrated=sum(1 for r in rows if r.get("claim_ok") is True),
        eligible_n=len(eligible),
        eligible_pass=sum(1 for r in eligible if r.get("claim_ok") is True),
        na=sum(1 for r in rows if r.get("claim_ok") is None),
        raw_pass=sum(1 for r in rows if r.get("raw_spec_ok")),
        tool_pass=sum(1 for r in eligible if r.get("tool_ok")),
    )


def aggregate(rows: Iterable[dict], *, chain_len: int = 7) -> GateReport:
    """Per-step summaries plus the two full-chain rates and the gate verdict.

    `rows` are the per-turn dicts the harness records; each needs `run`, `step`,
    `eligible`, `claim_ok`, `raw_spec_ok`, `tool_ok`.

    Two verdict axes, never collapsed into one label. The run that motivated
    this module was simultaneously a behavioural failure (2 of 5 chains drew a
    chart) and an under-observed one (2 eligible samples on every later step).
    Reporting only "FAIL" hides the second; only "INSUFFICIENT SAMPLE" hides the
    first.
    """
    rows = list(rows)
    by_step: dict[int, list[dict]] = {}
    by_run: dict[Any, list[dict]] = {}
    for row in rows:
        by_step.setdefault(row["step"], []).append(row)
        # Keyed on (arm, run), not run alone. `--arm both` numbers its runs
        # 0..n-1 inside EACH arm, so a run-only key silently fuses the fast and
        # reasoning chains with the same index into one 14-row "chain" -- which
        # would then fail the length check and under-report full chains. The
        # on-screen report filters by arm first and never saw this; the summary
        # written into the results file did.
        by_run.setdefault((row.get("arm", ""), row["run"]), []).append(row)

    steps = [summarize_step(s, by_step[s]) for s in sorted(by_step)]
    by_index = {s.step: s for s in steps}

    def chain_complete(chain_rows: list[dict]) -> bool:
        if len(chain_rows) < chain_len:
            return False
        # A skipped step is not a passed step: the chain did not demonstrate it.
        return all(r.get("claim_ok") is True for r in chain_rows)

    total_chains = len(by_run)
    full_unconditional = sum(1 for r in by_run.values() if chain_complete(r))

    created = {
        run: chain_rows for run, chain_rows in by_run.items()
        if any(r["step"] == STEP_CREATE and r.get("claim_ok") is True for r in chain_rows)
    }
    full_conditional = sum(1 for r in created.values() if chain_complete(r))

    create = by_index.get(STEP_CREATE)
    outcome_ok = bool(create and create.demonstrated == create.total and create.total > 0)
    outcome_ok = outcome_ok and full_unconditional == total_chains and total_chains > 0
    for step in CONDITIONAL_STEPS:
        summary = by_index.get(step)
        if summary is not None and not summary.passed:
            outcome_ok = False

    # A conditional step that produced NO rows at all is not "covered by
    # default" -- the `if s in by_index` filter would drop it and let all()
    # return True over what is left, so a truncated run could report
    # SUFFICIENT. Absent means unobserved, which is the definition of
    # insufficient.
    coverage_ok = all(
        s in by_index and by_index[s].sufficient for s in CONDITIONAL_STEPS
    )

    return GateReport(
        steps=steps,
        full_chain_unconditional=(full_unconditional, total_chains),
        full_chain_conditional=(full_conditional, len(created)),
        outcome_status="PASS" if outcome_ok else "FAIL",
        coverage_status="SUFFICIENT" if coverage_ok else "INSUFFICIENT",
        gate_pass=bool(outcome_ok and coverage_ok),
    )
