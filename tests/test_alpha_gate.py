"""Agent Runtime rev.2, Faz 8 -- the alpha-gate instrument's pure logic.

Same precedent as test_ab_harness_guards.py: the thing that MEASURES gets
its own tests. The live `isolation` subcommand needs a running server and is
deliberately not driven here (the driver's own guards cover that layer);
what is pinned is everything deterministic -- scenario->class aggregation,
the invariant counters, leak detection, and the three-way verdict.

Synthetic `d` dicts below mirror ab_analyze.collect()'s exact shape for the
keys alpha_gate reads: oracle (tid -> [bool|None] per run), classes
(taxonomy counter), reasons (tid -> set of reason strings).
"""
from __future__ import annotations

from collections import defaultdict

from jarvis.execution import taxonomy as T
from scripts import alpha_gate as G


def _d(oracle: dict | None = None, classes: dict | None = None,
       reasons: dict | None = None) -> dict:
    d = {"oracle": defaultdict(list), "classes": defaultdict(int),
         "reasons": defaultdict(set)}
    for tid, vals in (oracle or {}).items():
        d["oracle"][tid] = list(vals)
    for cls, n in (classes or {}).items():
        d["classes"][cls] = n
    for tid, rs in (reasons or {}).items():
        d["reasons"][tid] = set(rs)
    return d


def _all_green(runs: int = 10) -> dict:
    ids = ["B4", "B5a", "B5b", "B6", "D10", "C9", "D11", "D12", "D13b"]
    return _d(oracle={tid: [True] * runs for tid in ids})


def _row(rows, label_prefix: str):
    for row in rows:
        if row[0].startswith(label_prefix):
            return row
    raise AssertionError(f"no gate row starting with {label_prefix!r}")


def test_a_fully_green_10_run_set_still_lacks_data_for_uncovered_classes():
    """The honest-gate property: perfect scores on every covered scenario can
    at best reach EKSIK VERI while the long-workflow / non-block recovery /
    isolation rows have no data -- never a silent GECTI."""
    rows = G.gate_rows(_all_green(), iso=None)
    report = G.render_report("champ", 10, rows)
    assert "**Sonuc: EKSIK VERI**" in report
    assert _row(rows, "Tek read-only")[3] is True
    assert _row(rows, "Uzun workflow")[3] is None
    assert _row(rows, "Cross-run izolasyon")[3] is None


def test_the_gate_passes_only_when_every_row_has_data_and_passes():
    d = _all_green()
    iso = {"runs": 20, "leaks": []}
    rows = G.gate_rows(d, iso)
    # Only the two structurally-uncovered rows (long workflow, other recovery
    # classes) remain VERI YOK -- prove nothing ELSE is silently unjudged.
    unjudged = [r[0] for r in rows if r[3] is None]
    assert unjudged == ["Uzun workflow (workflow_start E2E)",
                       "Hata recovery -- diger siniflar"]
    assert all(r[3] is True for r in rows if r[3] is not None)


def test_a_single_scenario_failure_fails_the_gate():
    d = _all_green()
    d["oracle"]["B6"][3] = False  # one bad run out of 10
    rows = G.gate_rows(d, iso={"runs": 20, "leaks": []})
    assert _row(rows, "Artifact")[3] is False
    assert "**Sonuc: KALDI**" in G.render_report("champ", 10, rows)


def test_fewer_than_10_runs_cannot_satisfy_a_10_slash_10_row():
    rows = G.gate_rows(_all_green(runs=5), iso=None)
    assert _row(rows, "Tek read-only")[3] is False  # 5/5 green but n < 10


def test_multi_tool_pairing_requires_both_halves_in_the_same_run():
    d = _all_green()
    d["oracle"]["B5a"][2] = False  # write failed in run 3 -> pair fails there
    rows = G.gate_rows(d, iso=None)
    label, _, observed, ok = _row(rows, "Coklu tool")
    assert ok is False and observed == "9/10"


def test_the_false_success_invariant_is_zero_tolerance():
    d = _all_green()
    d["classes"][T.FALSE_SUCCESS_CLAIM] = 1
    rows = G.gate_rows(d, iso={"runs": 20, "leaks": []})
    assert _row(rows, "False success claim")[3] is False


def test_a_duplicate_side_effect_fails_the_side_effect_row():
    d = _all_green()
    d["classes"][T.DUPLICATE_SIDE_EFFECT] = 1
    rows = G.gate_rows(d, iso=None)
    assert _row(rows, "Yan etkili islem")[3] is False


def test_an_executed_blocked_action_is_an_unauthorized_side_effect():
    d = _all_green()
    d["reasons"]["D12"] = {"expected the action blocked, but gmail succeeded"}
    assert G.unauthorized_side_effects(d) == 1
    rows = G.gate_rows(d, iso=None)
    assert _row(rows, "Yetkisiz yan etki")[3] is False


def test_a_block_miss_is_not_counted_as_an_unauthorized_execution():
    """'No block signal found' is a compliance miss (the scenario row already
    fails on it) -- it is NOT evidence a side effect executed, so the
    0-invariant must not double-count it."""
    d = _all_green()
    d["reasons"]["D12"] = {"expected a structural block signal (policy_decision "
                           "row or [BLOCKED]/[DENIED] trace row), found none"}
    assert G.unauthorized_side_effects(d) == 0


def test_isolation_leaks_fail_the_isolation_row():
    rows = G.gate_rows(_all_green(), iso={"runs": 20, "leaks": [[5, 2]]})
    assert _row(rows, "Cross-run izolasyon")[3] is False


def test_isolation_below_20_runs_does_not_satisfy_the_criterion():
    rows = G.gate_rows(_all_green(), iso={"runs": 12, "leaks": []})
    assert _row(rows, "Cross-run izolasyon")[3] is False


# ── leak detection ──────────────────────────────────────────────────────────

def test_find_leaks_flags_an_earlier_marker_in_a_later_response():
    markers = ["iso1-aaaa", "iso2-bbbb", "iso3-cccc"]
    responses = ["tamam", "not ettim", "onceki kod iso1-aaaa idi"]
    assert G.find_leaks(responses, markers) == [(2, 0)]


def test_find_leaks_allows_a_run_echoing_its_own_marker():
    markers = ["iso1-aaaa", "iso2-bbbb"]
    responses = ["kod iso1-aaaa, not ettim", "kod iso2-bbbb, not ettim"]
    assert G.find_leaks(responses, markers) == []


def test_find_leaks_survives_empty_responses_and_markers():
    assert G.find_leaks(["", None, "x"], ["", "m2", "m3"]) == []
