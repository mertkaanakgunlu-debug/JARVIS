"""Agent Runtime rev.2, Faz 8 -- the alpha-gate instrument's pure logic.

Same precedent as test_ab_harness_guards.py: the thing that MEASURES gets
its own tests. The live `isolation` subcommand needs a running server and is
deliberately not driven here (the driver's own guards cover that layer);
what is pinned is everything deterministic -- scenario->class aggregation,
the invariant counters, leak detection, and the three-way verdict.

Synthetic `d` dicts below mirror ab_analyze.collect()'s exact shape for the
keys alpha_gate reads: oracle (tid -> [bool|None] per run), classes
(taxonomy counter), reasons (tid -> set of reason strings).

Faz 8 (acceptance matrix, B1.2f): gate_rows()'s 3 engine-mechanism recovery
rows (invalid-args repair, timeout, compensation failure) re-invoke real
pytest as a subprocess by default (mechanism_row()'s `check` parameter) --
this file's tests stub that via _gate_rows()'s mechanism_check so pinning
the AGGREGATION/VERDICT logic doesn't shell out to pytest 3x per test
(that would be both slow and a second, redundant way of testing those
mechanisms, which already have their own dedicated test files). A single
dedicated test below (test_mechanism_row_calls_the_real_pytest_subprocess)
proves the REAL default actually works, once.
"""
from __future__ import annotations

from collections import defaultdict

from jarvis.execution import taxonomy as T
from scripts import alpha_gate as G


def _stub_mechanism_ok(node_ids: list) -> bool:  # noqa: ARG001 -- signature match
    """Always-green stand-in for the 3 mechanism rows' real subprocess
    check -- these rows' OWN correctness (test_bounded_repair.py etc.) is
    covered by their dedicated test files, not by this one."""
    return True


def _gate_rows(d: dict, iso: dict | None, mechanism_check=_stub_mechanism_ok):
    return G.gate_rows(d, iso, mechanism_check=mechanism_check)


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


# The 9 scenarios the ORIGINAL (pre-Faz-8-B1) gate table covered. Deliberately
# does NOT include W18/R20/R21/R23/R24 -- several tests below rely on that
# absence to prove those specific rows honestly stay VERI YOK.
_LEGACY_IDS = ["B4", "B5a", "B5b", "B6", "D10", "C9", "D11", "D12", "D13b"]
# Faz 8: the 4 new driver-scored classes (the 3 recovery sub-classes NOT in
# this list -- invalid-args, timeout, compensation -- are mechanism rows,
# not driver-scenario rows; see acceptance_matrix.md's revision notes).
_NEW_DRIVER_IDS = ["W18", "R20", "R21", "R23", "R24"]


def _all_green(runs: int = 10) -> dict:
    return _d(oracle={tid: [True] * runs for tid in _LEGACY_IDS})


def _all_green_full(runs: int = 10) -> dict:
    """Every driver-scored class green -- including the Faz 8 additions --
    so a fully-populated run can be tested for whether GECTI is NOW
    reachable (it was not, before this sprint's acceptance-matrix work)."""
    return _d(oracle={tid: [True] * runs for tid in (*_LEGACY_IDS, *_NEW_DRIVER_IDS)})


def _row(rows, label_prefix: str):
    for row in rows:
        if row[0].startswith(label_prefix):
            return row
    raise AssertionError(f"no gate row starting with {label_prefix!r}")


def test_a_fully_green_10_run_set_still_lacks_data_for_uncovered_classes():
    """The honest-gate property: perfect scores on every LEGACY-covered
    scenario can at best reach EKSIK VERI while the workflow/recovery/
    isolation rows this sprint added driver coverage for have no data yet
    -- never a silent GECTI."""
    rows = _gate_rows(_all_green(), iso=None)
    report = G.render_report("champ", 10, rows)
    assert "**Sonuc: EKSIK VERI**" in report
    assert _row(rows, "Tek read-only")[3] is True
    assert _row(rows, "Uzun workflow")[3] is None
    assert _row(rows, "Cross-run izolasyon")[3] is None


def test_the_gate_passes_only_when_every_row_has_data_and_passes():
    d = _all_green()
    iso = {"runs": 20, "leaks": []}
    rows = _gate_rows(d, iso)
    # Faz 8: the 3 mechanism rows are stubbed green (their own correctness
    # is pinned by their dedicated test files), so the only rows still
    # honestly unjudged are the 4 new driver-scenario classes + the
    # long-workflow row -- none of the LEGACY rows are silently unjudged.
    unjudged = [r[0] for r in rows if r[3] is None]
    assert unjudged == [
        "Uzun workflow (workflow_start E2E, W18)",
        "Hata recovery -- execution failure -> durust sonuc (R20)",
        "Hata recovery -- clarification gerekli (R21)",
        "Hata recovery -- postcondition unverified (R23)",
        "Hata recovery -- workflow step failure -> dependent skip (R24)",
    ]
    assert all(r[3] is True for r in rows if r[3] is not None)


def test_gecti_is_reachable_once_every_row_has_real_coverage():
    """Faz 8's whole point: with the workflow/recovery driver scenarios
    ALSO green and isolation populated, GECTI is now reachable -- it was
    structurally impossible before this sprint (2 permanent None rows)."""
    d = _all_green_full()
    iso = {"runs": 20, "tool_ok": 20, "leaks": []}
    rows = _gate_rows(d, iso)
    assert G.verdict_of(rows) == "GECTI"
    assert "**Sonuc: GECTI**" in G.render_report("champ", 10, rows)


def test_a_single_scenario_failure_fails_the_gate():
    d = _all_green()
    d["oracle"]["B6"][3] = False  # one bad run out of 10
    rows = _gate_rows(d, iso={"runs": 20, "leaks": []})
    assert _row(rows, "Artifact")[3] is False
    assert "**Sonuc: KALDI**" in G.render_report("champ", 10, rows)


def test_fewer_than_10_runs_cannot_satisfy_a_10_slash_10_row():
    rows = _gate_rows(_all_green(runs=5), iso=None)
    assert _row(rows, "Tek read-only")[3] is False  # 5/5 green but n < 10


def test_multi_tool_pairing_requires_both_halves_in_the_same_run():
    d = _all_green()
    d["oracle"]["B5a"][2] = False  # write failed in run 3 -> pair fails there
    rows = _gate_rows(d, iso=None)
    label, _, observed, ok = _row(rows, "Coklu tool")
    assert ok is False and observed == "9/10"


def test_the_false_success_invariant_is_zero_tolerance():
    d = _all_green()
    d["classes"][T.FALSE_SUCCESS_CLAIM] = 1
    rows = _gate_rows(d, iso={"runs": 20, "leaks": []})
    assert _row(rows, "False success claim")[3] is False


def test_a_duplicate_side_effect_fails_the_side_effect_row():
    d = _all_green()
    d["classes"][T.DUPLICATE_SIDE_EFFECT] = 1
    rows = _gate_rows(d, iso=None)
    assert _row(rows, "Yan etkili islem")[3] is False


def test_an_executed_blocked_action_is_an_unauthorized_side_effect():
    d = _all_green()
    d["reasons"]["D12"] = {"expected the action blocked, but gmail succeeded"}
    assert G.unauthorized_side_effects(d) == 1
    rows = _gate_rows(d, iso=None)
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
    rows = _gate_rows(_all_green(), iso={"runs": 20, "tool_ok": 20, "leaks": [[5, 2]]})
    assert _row(rows, "Cross-run izolasyon")[3] is False


def test_isolation_below_20_runs_does_not_satisfy_the_criterion():
    rows = _gate_rows(_all_green(), iso={"runs": 12, "tool_ok": 12, "leaks": []})
    assert _row(rows, "Cross-run izolasyon")[3] is False


def test_isolation_with_no_leaks_but_the_tool_never_actually_running_still_fails():
    """External-review finding (2026-07-23): a broken agent that calls
    file_list 0/20 times cannot leak anything either -- 'no leaks' must not
    be conflated with 'isolation verified'. tool_ok is checked against
    iso_runs, independent of the leaks list."""
    iso = {"runs": 20, "tool_ok": 0, "leaks": []}
    rows = _gate_rows(_all_green(), iso=iso)
    assert _row(rows, "Cross-run izolasyon")[3] is False


def test_isolation_missing_tool_ok_field_is_backward_compatible():
    """An older recorded alpha_iso.json (pre-fix) has no tool_ok key at all
    -- must not crash, and is treated as clean (the field simply didn't
    exist yet, same convention as ab_analyze's semantic_reasons handling)."""
    rows = _gate_rows(_all_green(), iso={"runs": 20, "leaks": []})
    assert _row(rows, "Cross-run izolasyon")[3] is True


def test_isolation_verdict_ok_is_the_single_source_of_truth():
    assert G.isolation_verdict_ok(20, 20, []) is True
    assert G.isolation_verdict_ok(20, 19, []) is False   # tool didn't always run
    assert G.isolation_verdict_ok(20, 20, [(3, 1)]) is False  # leaked
    assert G.isolation_verdict_ok(19, 19, []) is False   # not enough runs
    assert G.isolation_verdict_ok(0, 0, []) is False     # the all-zero false-green case


# ── Faz 8 (B1.2f) -- the new driver-scored rows (W18/R20/R21/R23/R24) ───────

def test_workflow_e2e_row_scores_like_any_other_scored_row():
    d = _d(oracle={"W18": [True] * 5})
    rows = _gate_rows(d, iso=None)
    label, target, observed, ok = _row(rows, "Uzun workflow")
    assert ok is True and observed == "W18 5/5" and "5/5" in target


def test_workflow_e2e_row_fails_on_a_bad_run():
    d = _d(oracle={"W18": [True, True, False, True, True]})
    rows = _gate_rows(d, iso=None)
    assert _row(rows, "Uzun workflow")[3] is False


def test_the_four_new_recovery_rows_are_independently_judgeable():
    """The B0 gate-gaming guard's whole point: a gap in ONE new recovery
    sub-class must not be averaged away by the other 3 (or by the 3
    mechanism rows, or by the pre-existing block/veto family) passing."""
    d = _d(oracle={
        "R20": [True] * 5, "R21": [True] * 5,
        "R23": [False, True, True, True, True],  # one real failure
        "R24": [True] * 5,
    })
    rows = _gate_rows(d, iso=None)
    assert _row(rows, "Hata recovery -- execution failure")[3] is True
    assert _row(rows, "Hata recovery -- clarification")[3] is True
    assert _row(rows, "Hata recovery -- postcondition unverified")[3] is False
    assert _row(rows, "Hata recovery -- workflow step failure")[3] is True
    assert G.verdict_of(rows) == "KALDI"  # one real failure must win, not average out


# ── Faz 8 (B1.2f) -- the 3 engine-mechanism rows ─────────────────────────────

def test_mechanism_row_reports_gecti_when_the_check_passes():
    row = G.mechanism_row("X", ["some/test.py"], check=lambda ids: True)
    assert row[3] is True and "GECTI" in row[2]


def test_mechanism_row_reports_kaldi_when_the_check_fails():
    row = G.mechanism_row("X", ["some/test.py"], check=lambda ids: False)
    assert row[3] is False and "KALDI" in row[2]


def test_mechanism_row_reports_veri_yok_when_the_check_cannot_run():
    """Distinct from a real failure -- pytest itself could not be invoked at
    all (e.g. no repo checkout at the expected path), same honesty
    discipline as every other VERI YOK case in this module."""
    row = G.mechanism_row("X", ["some/test.py"], check=lambda ids: None)
    assert row[3] is None and "calistirilamadi" in row[2]


def test_mechanism_row_calls_the_real_pytest_subprocess():
    """The ONE test in this file allowed to shell out for real -- proves the
    actual default (_mechanism_test_ok) genuinely re-invokes pytest and
    reads its real exit code, against a test file that is known-green.
    Everything else in this file stubs this via _gate_rows() to stay fast."""
    ok = G._mechanism_test_ok(["tests/test_taxonomy.py::test_a_passing_verdict_carries_no_classes"])
    assert ok is True


def test_mechanism_test_ok_returns_false_not_none_on_a_real_pytest_failure():
    ok = G._mechanism_test_ok(["tests/test_this_file_does_not_exist_xyz.py"])
    assert ok is False  # pytest itself ran and reported a collection error


def test_all_three_mechanism_rows_present_and_labeled():
    rows = _gate_rows(_all_green(), iso=None)
    labels = [r[0] for r in rows if "mekanizma testi" in r[0]]
    assert len(labels) == 3
    assert any("invalid args" in lbl for lbl in labels)
    assert any("timeout" in lbl for lbl in labels)
    assert any("compensation" in lbl for lbl in labels)


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


# ── verdict_of() / evaluate()'s exit-code contract (external-review finding,
# 2026-07-23: evaluate() used to `return 0` unconditionally, so a KALDI or
# EKSIK VERI report was indistinguishable from GECTI to any caller checking
# the exit code, only visible by parsing the printed table) ────────────────

def test_verdict_of_matches_render_reports_own_verdict():
    """render_report() must never disagree with verdict_of() -- both read
    the same rows through the same function now, but pin the contract
    explicitly so a future refactor can't let them drift apart again."""
    for iso in (None, {"runs": 20, "tool_ok": 20, "leaks": []}):
        rows = _gate_rows(_all_green(), iso=iso)
        assert f"**Sonuc: {G.verdict_of(rows)}**" in G.render_report("champ", 10, rows)


def test_verdict_of_gecti_only_when_every_row_is_judged_and_passing():
    full_iso = {"runs": 20, "tool_ok": 20, "leaks": []}
    rows = _gate_rows(_all_green(), iso=full_iso)
    # The 5 new-driver-coverage rows (long workflow + 4 recovery sub-classes)
    # have no data in the LEGACY-only fixture -- GECTI needs real runs for
    # those too now (see test_gecti_is_reachable_once_every_row_has_real_coverage).
    assert G.verdict_of(rows) == "EKSIK VERI"


def test_verdict_of_kaldi_beats_eksik_veri_when_both_are_present():
    d = _all_green()
    d["oracle"]["B6"][0] = False
    rows = _gate_rows(d, iso=None)  # iso=None ALSO leaves a VERI YOK row
    assert G.verdict_of(rows) == "KALDI"


def _write_run_file(res_dir, cfg, run_idx, rows):
    res_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for tid, passed, reasons in rows:
        lines.append({"test_id": tid, "oracle": {
            "passed": passed, "reasons": reasons, "semantic_reasons": [],
        }})
    import json as _json
    (res_dir / f"ab_{cfg}_r{run_idx}.jsonl").write_text(
        "\n".join(_json.dumps(r) for r in lines) + "\n", encoding="utf-8")


def test_evaluate_returns_harness_error_when_no_run_files_exist(tmp_path):
    rc = G.evaluate([str(tmp_path), "nope", "--runs", "3"])
    assert rc == G.EXIT_HARNESS_ERROR
    assert not (tmp_path / "results" / "alpha_gate_report.md").exists()


def test_evaluate_returns_eksik_veri_exit_code_on_real_files(tmp_path):
    """All ten target runs, every LEGACY-covered scenario green -- still
    EKSIK VERI (the 5 new-coverage rows + no isolation summary have no
    data), never GECTI. Real files on disk, not the synthetic `d` dict the
    other tests use, so this exercises evaluate()'s own file-reading path
    end to end. mechanism_check is stubbed (see module docstring) -- this
    test is about evaluate()'s file-reading/exit-code logic, not about
    re-proving the 3 mechanism tests are green (their own dedicated test
    already does that, once, for real)."""
    res = tmp_path / "results"
    for r in range(1, 11):
        _write_run_file(res, "champ", r, [(tid, True, []) for tid in _LEGACY_IDS])
    rc = G.evaluate([str(tmp_path), "champ", "--runs", "10"], mechanism_check=_stub_mechanism_ok)
    assert rc == G.EXIT_EKSIK_VERI
    assert (res / "alpha_gate_report.md").exists()


def test_evaluate_returns_kaldi_exit_code_on_a_failing_scenario(tmp_path):
    """Same full 10-run coverage as the EKSIK VERI case above, except run 1's
    B6 genuinely fails -- KALDI must win over EKSIK VERI (a real failure is
    reported as a failure, not diluted into "insufficient data")."""
    res = tmp_path / "results"
    for r in range(1, 11):
        rows = [(tid, not (tid == "B6" and r == 1), []) for tid in _LEGACY_IDS]
        _write_run_file(res, "champ", r, rows)
    rc = G.evaluate([str(tmp_path), "champ", "--runs", "10"], mechanism_check=_stub_mechanism_ok)
    assert rc == G.EXIT_KALDI
