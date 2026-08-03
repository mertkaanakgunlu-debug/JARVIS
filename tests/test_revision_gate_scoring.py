"""The Faz 4 gate's scoring, and the vacuous passes it used to report.

Regime A (deterministic). This is the layer that let a wrong number reach a
handoff document: three chains that never drew a chart reported the "must not
touch" and "undo" steps as successes, because `spec == previous` is trivially
true when both are `{}`. The run printed `undo 5/5` while only two chains had
ever held a chart.

Imports `jarvis.evals.revision_scoring`, NOT `scripts/revision_gate.py` -- that
script sets JARVIS_HOME/CLOUD_POLICY, calls os.chdir() and monkeypatches a
global callback class at import time, so a test that touched it would inherit
all of that.

The organising idea: a step's number must answer "did this DEMONSTRATE the
claim", not "did the post-state happen to look right". Those differ exactly
when the step was never in a position to show anything.
"""
from __future__ import annotations

import pytest

from jarvis.evals.revision_scoring import (
    MIN_ELIGIBLE,
    STEP_COLOUR,
    STEP_CREATE,
    STEP_KIND,
    STEP_THANKS,
    STEP_TITLE,
    STEP_UNDO,
    STEP_UNRELATED,
    aggregate,
    eligibility_for,
    score_step,
)
from jarvis.working_set import KIND_CHART, Revision, WorkingObject


def _chart(spec: dict | None = None, history: tuple = ()) -> WorkingObject:
    return WorkingObject(
        id="ab12c3", conversation_id="c1", kind=KIND_CHART,
        title="t", spec=dict(spec or {"source": "s.csv", "x": "ay", "y": "satis"}),
        revision_history=history,
    )


def _bar_transition() -> Revision:
    return Revision(version=4, changes={"kind": "bar"}, before={"kind": "line"}, at="")


def _colour_revision() -> Revision:
    return Revision(version=2, changes={"color": "kırmızı"}, before={}, at="")


# ── No chart at all: the original vacuous pass ───────────────────────────────

@pytest.mark.parametrize("step", [STEP_COLOUR, STEP_TITLE, STEP_KIND,
                                  STEP_UNRELATED, STEP_THANKS, STEP_UNDO])
def test_without_a_chart_no_step_can_demonstrate_anything(step):
    """THE regression. Every one of these used to score as a pass against an
    empty spec."""
    score = score_step(step=step, pre_obj=None, raw_spec_ok=True, raw_tool_ok=True)

    assert score.eligible is False
    assert score.skip_reason == "no active chart"
    assert score.spec_ok is None
    assert score.claim_ok is None


def test_creation_is_always_eligible():
    """Step 0 is the one step judged over every run -- it is what the later
    steps' eligibility depends on, so it can never itself be skipped."""
    eligible, reason = eligibility_for(STEP_CREATE, None)
    assert eligible is True and reason == ""


# ── Already-satisfied targets ────────────────────────────────────────────────

@pytest.mark.parametrize(("step", "spec", "reason"), [
    (STEP_COLOUR, {"color": "kırmızı"}, "target already red"),
    (STEP_COLOUR, {"color": "red"}, "target already red"),
    (STEP_TITLE, {"title": "2026 Satışları"}, "target title already set"),
    (STEP_KIND, {"kind": "bar"}, "target already bar"),
])
def test_an_already_satisfied_revision_is_not_evidence(step, spec, reason):
    """Turn 0 does not constrain the chart type -- the harness deliberately
    scores only "an object exists" -- so a model that opens with a bar chart
    passes "sütun grafiği olsun" without calling anything."""
    score = score_step(step=step, pre_obj=_chart(spec), raw_spec_ok=True, raw_tool_ok=False)

    assert score.eligible is False
    assert score.skip_reason == reason
    assert score.claim_ok is None


@pytest.mark.parametrize(("step", "spec"), [
    (STEP_COLOUR, {"color": "mavi"}),
    (STEP_COLOUR, {}),
    (STEP_TITLE, {"title": "Aylık Satış"}),
    (STEP_KIND, {"kind": "line"}),
])
def test_an_unmet_target_is_eligible(step, spec):
    assert eligibility_for(step, _chart(spec))[0] is True


# ── Undo needs a real transition to take back ────────────────────────────────

def test_undo_without_a_bar_state_is_not_evidence():
    obj = _chart({"kind": "line"}, history=(_colour_revision(),))
    assert eligibility_for(STEP_UNDO, obj) == (False, "no bar state to undo")


def test_undo_with_no_history_is_not_evidence():
    assert eligibility_for(STEP_UNDO, _chart({"kind": "bar"})) == (False, "nothing to undo")


def test_undo_needs_a_real_kind_transition_not_just_any_history():
    """The chart was drawn as a bar at turn 0, so the kind revision never
    happened and history holds only the colour change -- there is no bar
    transition to take back."""
    obj = _chart({"kind": "bar"}, history=(_colour_revision(),))
    assert eligibility_for(STEP_UNDO, obj) == (False, "no bar revision to undo")


def test_undo_with_a_real_transition_is_eligible():
    obj = _chart({"kind": "bar"}, history=(_colour_revision(), _bar_transition()))
    assert eligibility_for(STEP_UNDO, obj) == (True, "")


def test_a_later_unrelated_revision_does_not_make_undo_ineligible():
    """If "teşekkürler" wrongly appended a revision, undo takes back the WRONG
    one -- that must surface as a failure, not be hidden behind N/A."""
    stray = Revision(version=5, changes={"title": "x"}, before={"title": "y"}, at="")
    obj = _chart({"kind": "bar"}, history=(_bar_transition(), stray))

    assert eligibility_for(STEP_UNDO, obj) == (True, "")
    score = score_step(step=STEP_UNDO, pre_obj=obj, raw_spec_ok=False, raw_tool_ok=True)
    assert score.claim_ok is False, "a wrong undo must be a failure, not a skip"


# ── "Must not touch" claims two things ───────────────────────────────────────

def test_an_untouched_spec_with_a_chart_tool_call_is_not_a_success():
    """The claim is "no chart tool ran AND nothing moved". A model that called
    chart_revise and happened to leave the spec identical broke the rule."""
    score = score_step(step=STEP_UNRELATED, pre_obj=_chart(),
                       raw_spec_ok=True, raw_tool_ok=False)

    assert score.spec_ok is True
    assert score.tool_ok is False
    assert score.claim_ok is False


def test_a_revision_step_is_not_gated_on_the_tool_axis():
    """Which tool the model reached for is reported, not claimed -- a redraw
    via plot_data that lands the right spec is a success."""
    score = score_step(step=STEP_COLOUR, pre_obj=_chart(), raw_spec_ok=True, raw_tool_ok=False)
    assert score.claim_ok is True


# ── The raw checker result survives as a diagnostic ──────────────────────────

def test_an_ineligible_row_keeps_the_raw_result_for_diagnosis():
    score = score_step(step=STEP_UNRELATED, pre_obj=None, raw_spec_ok=True, raw_tool_ok=True)

    assert score.eligible is False
    assert score.raw_spec_ok is True, "raw output must survive for the raw-check column"
    assert score.spec_ok is None
    assert score.claim_ok is None


# ── Aggregation ──────────────────────────────────────────────────────────────

def _row(run, step, *, eligible=True, claim=True, raw=True, tool=True):
    return {"run": run, "step": step, "eligible": eligible,
            "claim_ok": (claim if eligible else None),
            "raw_spec_ok": raw, "tool_ok": (tool if eligible else None)}


def _chain(run, *, created=True, eligible_after=True):
    rows = [_row(run, STEP_CREATE, claim=created)]
    for step in range(1, 7):
        rows.append(_row(run, step, eligible=eligible_after, raw=True))
    return rows


def test_two_eligible_passes_and_three_na_report_honestly():
    rows = []
    for run in (0, 1, 2):
        rows.extend(_chain(run, created=False, eligible_after=False))
    for run in (3, 4):
        rows.extend(_chain(run, created=True, eligible_after=True))

    report = aggregate(rows)
    undo = next(s for s in report.steps if s.step == STEP_UNDO)

    assert (undo.demonstrated, undo.total) == (2, 5)
    assert (undo.eligible_pass, undo.eligible_n) == (2, 2)
    assert undo.na == 3
    assert undo.raw_pass == 5, "the vacuous raw result is kept as a diagnostic"


def test_insufficient_coverage_and_hard_failure_are_reported_separately():
    """The run that motivated this module was both at once."""
    rows = []
    for run in (0, 1, 2):
        rows.extend(_chain(run, created=False, eligible_after=False))
    for run in (3, 4):
        rows.extend(_chain(run, created=True, eligible_after=True))

    report = aggregate(rows)

    assert report.outcome_status == "FAIL"        # creation 2/5
    assert report.coverage_status == "INSUFFICIENT"  # only 2 eligible
    assert report.gate_pass is False


def test_a_chain_with_a_skipped_step_is_not_a_full_chain():
    rows = _chain(0, created=True, eligible_after=True)
    rows[STEP_UNDO]["eligible"] = False
    rows[STEP_UNDO]["claim_ok"] = None

    report = aggregate(rows)

    assert report.full_chain_unconditional == (0, 1), "an N/A step cannot complete a chain"


def test_conditional_denominator_is_only_the_chains_that_created():
    rows = []
    for run in (0, 1, 2):
        rows.extend(_chain(run, created=False, eligible_after=False))
    for run in (3, 4):
        rows.extend(_chain(run, created=True, eligible_after=True))

    report = aggregate(rows)

    assert report.full_chain_unconditional == (2, 5)
    assert report.full_chain_conditional == (2, 2)


def test_a_clean_run_with_enough_samples_passes():
    rows = []
    for run in range(MIN_ELIGIBLE):
        rows.extend(_chain(run, created=True, eligible_after=True))

    report = aggregate(rows)

    assert report.outcome_status == "PASS"
    assert report.coverage_status == "SUFFICIENT"
    assert report.gate_pass is True


def test_two_arms_with_colliding_run_indices_stay_separate_chains():
    """`--arm both` numbers runs 0..n-1 inside EACH arm. Grouping on `run`
    alone fused fast#0 and reasoning#0 into one 14-row "chain", which then
    failed the length check -- the on-screen report filtered by arm first and
    never saw it, the stored summary did."""
    rows = []
    for arm in ("fast", "reasoning"):
        for run in range(MIN_ELIGIBLE):
            chain = _chain(run, created=True, eligible_after=True)
            for row in chain:
                row["arm"] = arm
            rows.extend(chain)

    report = aggregate(rows)

    assert report.full_chain_unconditional == (6, 6), "chains were fused across arms"
    assert report.gate_pass is True


def test_a_conditional_step_with_no_rows_is_not_silently_covered():
    """An absent step is unobserved, which is the definition of insufficient --
    the previous `if s in by_index` filter dropped it and let all() pass."""
    rows = []
    for run in range(MIN_ELIGIBLE):
        chain = _chain(run, created=True, eligible_after=True)
        rows.extend(r for r in chain if r["step"] != STEP_UNDO)   # run truncated

    report = aggregate(rows)

    assert report.coverage_status == "INSUFFICIENT"
    assert report.gate_pass is False


def test_enough_samples_but_a_real_failure_still_fails():
    rows = []
    for run in range(MIN_ELIGIBLE):
        rows.extend(_chain(run, created=True, eligible_after=True))
    rows[STEP_COLOUR]["claim_ok"] = False

    report = aggregate(rows)

    assert report.outcome_status == "FAIL"
    assert report.coverage_status == "SUFFICIENT"
    assert report.gate_pass is False
