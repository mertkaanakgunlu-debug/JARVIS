"""The completion contract's classifier — one requirement in, one verdict out.

Every way a required output can be missing belongs to a different layer, and
the whole point of this table is that they do not collapse into each other:
a repair is offered for exactly one of them. These tests are the pure half
(no graph, no store, no model); tests/test_output_contract_graph.py covers
the routing, the budget and the streaming surface.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from jarvis.execution.output_contract import (
    ContractVerdict,
    canonical,
    classify,
    turn_tool_calls,
)

CHART = [{"kind": "chart", "operation": "create"}]
CREATE = frozenset({"plot_data"})

PNG = "C:/tmp/run-1/chart.png"
OTHER_PNG = "C:/tmp/run-0/older.png"


def _ai(*calls) -> AIMessage:
    return AIMessage(content="", tool_calls=[
        {"name": name, "args": {}, "id": call_id} for name, call_id in calls
    ])


def _result(call_id: str, content: str = "chart drawn", *, artifact=None) -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id, artifact=artifact)


def _chart_artifact(path: str = PNG) -> list[dict]:
    return [{"path": path, "kind": "chart", "produced_by": "plot_data"}]


def _drew(call_id: str = "c1", path: str = PNG):
    """The happy path as messages: one plot_data call that rendered `path`."""
    return [_ai(("plot_data", call_id)), _result(call_id, artifact=_chart_artifact(path))]


def _classify(**overrides) -> ContractVerdict:
    kwargs = {"required": CHART, "capabilities": CREATE}
    kwargs.update(overrides)
    return classify(**kwargs)


# ── 8: no requirement, no verdict ──────────────────────────────────────────

@pytest.mark.parametrize("required", [None, [], [{"kind": "report", "operation": "create"}],
                                      [{"kind": "chart", "operation": "revise"}], ["chart"]])
def test_without_a_recognised_requirement_the_contract_is_not_required(required):
    """An unknown kind/operation must degrade to NOT_REQUIRED, never raise and
    never repair -- an old checkpoint or a future requirement this build
    cannot verify has to resume as if the contract were absent."""
    assert _classify(required=required, messages=[]).status == "NOT_REQUIRED"


def test_not_required_counts_as_satisfied_but_is_not_repairable():
    verdict = _classify(required=[], messages=[])
    assert verdict.satisfied is True
    assert verdict.repairable is False


# ── 1: the class this whole contract exists for ────────────────────────────

def test_an_explicit_request_with_no_attempt_is_the_repairable_case():
    verdict = _classify(messages=[HumanMessage(content="grafiği çiz"),
                                  AIMessage(content="Hangi formatta istersiniz?")])
    assert verdict.status == "MISSING_NO_ATTEMPT"
    assert verdict.repairable is True
    assert verdict.requirement == "chart/create"


# ── 7 / 2 / 21: success needs a declared artifact the working set kept ─────

def test_a_declared_artifact_registered_in_the_working_set_satisfies():
    verdict = _classify(messages=_drew(), registered_artifacts=[PNG])
    assert verdict.status == "SATISFIED"
    assert verdict.matched == (canonical(PNG),)
    assert verdict.repairable is False


def test_a_redraw_that_bumps_no_version_still_satisfies():
    """register_chart() skips patch() when the spec is unchanged and
    record_artifact() does not bump `version` -- so a genuine redraw leaves
    the object's id AND version identical. Scoring on version alone called
    that EXECUTED_NO_OBJECT: a working feature reported as a wiring defect.
    The new PNG path is the turn's own identity (plot_data writes a fresh run
    directory per call), so the artifact set is what moves.
    """
    verdict = _classify(
        messages=_drew(path=PNG),
        registered_artifacts=[OTHER_PNG, PNG],   # the old chart gained one
        baseline_artifacts=[OTHER_PNG],
    )
    assert verdict.status == "SATISFIED"


def test_paths_match_across_separator_and_case_differences():
    """Windows: the tool declares what it wrote, the store keeps what it was
    handed, and the two can differ in separators or drive-letter case."""
    declared = "C:/tmp/run-1/./chart.png"
    registered = "c:\\tmp\\run-1\\chart.png"
    assert canonical(declared) == canonical(registered)
    assert _classify(
        messages=_drew(path=declared), registered_artifacts=[registered],
    ).status == "SATISFIED"


def test_canonical_of_nothing_is_empty_and_never_matches():
    assert canonical("") == "" and canonical(None) == ""


# ── 11 / 22: what must NOT count as success ────────────────────────────────

def test_a_chart_left_over_from_an_earlier_turn_does_not_satisfy():
    """The user asked for a new chart; this turn drew nothing. A working set
    that still holds last turn's chart must not answer for this turn."""
    verdict = _classify(messages=[AIMessage(content="işte grafiğiniz")],
                        registered_artifacts=[OTHER_PNG],
                        baseline_artifacts=[OTHER_PNG])
    assert verdict.status == "MISSING_NO_ATTEMPT"


def test_revising_an_unrelated_chart_does_not_satisfy_a_creation_contract():
    """chart_revise bumps a version and can register an artifact, but it is
    not the requested postcondition: the model touching some older chart is
    not the same as producing the one that was asked for."""
    messages = [_ai(("chart_revise", "r1")),
                _result("r1", artifact=_chart_artifact(OTHER_PNG))]
    assert _classify(messages=messages,
                     registered_artifacts=[OTHER_PNG]).status == "MISSING_NO_ATTEMPT"


# ── 12 / 10 / 25: our defects, told apart from the model's ─────────────────

def test_a_declared_artifact_the_working_set_never_kept_is_our_postcondition_bug():
    """register_chart() logs and returns None rather than failing a chart that
    already drew. The file exists, the object does not, and every later
    revision fails -- that is a wiring defect, not something to re-prompt."""
    verdict = _classify(messages=_drew(), registered_artifacts=[])
    assert verdict.status == "EXECUTED_NO_OBJECT"
    assert verdict.declared == (canonical(PNG),)
    assert verdict.repairable is False


def test_a_success_that_declared_nothing_is_an_evidence_gap_not_a_failure():
    verdict = _classify(messages=[_ai(("plot_data", "c1")), _result("c1")],
                        registered_artifacts=[PNG])
    assert verdict.status == "OUTPUT_EVIDENCE_MISMATCH"
    assert verdict.repairable is False


@pytest.mark.parametrize("payload", ["not-a-list", {"path": PNG}, [None], [{"nope": 1}], 42])
def test_an_unreadable_artifact_payload_degrades_instead_of_crashing(payload):
    """parse_refs is tolerant by design (SqliteSaver round-trips it to plain
    dicts, old checkpoints predate the field). Unreadable evidence must read
    as 'no evidence', never as a false success and never as an exception."""
    verdict = _classify(messages=[_ai(("plot_data", "c1")), _result("c1", artifact=payload)],
                        registered_artifacts=[PNG])
    assert verdict.status == "OUTPUT_EVIDENCE_MISMATCH"


def test_evidence_that_could_not_be_read_never_blames_the_model():
    verdict = _classify(messages=[], evidence_error="OperationalError")
    assert verdict.status == "EVIDENCE_UNAVAILABLE"
    assert verdict.reason == "OperationalError"
    assert verdict.repairable is False


# ── 6 / 4 / 14 / 15: the three "someone else's layer" cases ────────────────

@pytest.mark.parametrize("content", ["[ERROR] Column 'Tarih' not found",
                                     "[TOOL_ERROR] category=timeout retryable=true"])
def test_an_honest_tool_error_is_never_repairable(content):
    """The honesty kernel rewards a tool that says it could not do the job.
    Re-driving the model because 'the output is missing' would punish exactly
    that behaviour."""
    verdict = _classify(messages=[_ai(("plot_data", "c1")), _result("c1", content)])
    assert verdict.status == "MISSING_TOOL_FAILURE"
    assert verdict.repairable is False


def test_rejected_args_belong_to_the_confirmation_layer():
    verdict = _classify(
        messages=[_ai(("plot_data", "c1")),
                  _result("c1", "[INVALID_ARGS: path] Field required")],
        invalid_args_history=[{"round": 1, "tool_call_id": "c1", "capability": "plot_data",
                               "errors": [{"loc": ["path"], "type": "missing"}]}],
    )
    assert verdict.status == "MISSING_INVALID_ARGS"
    assert verdict.repairable is False


@pytest.mark.parametrize("outcome", ["blocked_round_limit", "denied", "blocked_kill_switch"])
def test_a_call_stopped_before_execution_is_its_own_class(outcome):
    """A blocked call leaves the same failing stub a real error does, so the
    text cannot tell them apart -- the id-keyed history can. It matters: a
    user's refusal must never be retried, and a spent round budget will not
    be any freer on a second try."""
    verdict = _classify(
        messages=[_ai(("plot_data", "c1")), _result("c1", "[BLOCKED: not executed]")],
        preexecution_history=[{"round": 1, "tool_call_id": "c1",
                               "capability": "plot_data", "outcome": outcome}],
    )
    assert verdict.status == "MISSING_PREEXECUTION_BLOCK"
    assert verdict.reason == outcome
    assert verdict.repairable is False


def test_a_call_with_no_result_message_at_all_is_a_preexecution_block():
    """Whole-batch refusals stub every call, but a run cut short (recursion
    stop, interrupt) can leave a call unanswered. It was still attempted."""
    verdict = _classify(messages=[_ai(("plot_data", "c1"))])
    assert verdict.status == "MISSING_PREEXECUTION_BLOCK"


# ── 16: precedence when one turn does several things ───────────────────────

def test_the_later_execution_decides_not_the_earlier_rejection():
    """First attempt rejected on args, second executed but registered
    nothing. The verdict is about the requirement's final state, so the stale
    invalid-args record must not pull it back to that class."""
    messages = [
        _ai(("plot_data", "bad")), _result("bad", "[INVALID_ARGS: path] Field required"),
        _ai(("plot_data", "good")), _result("good", artifact=_chart_artifact()),
    ]
    verdict = _classify(
        messages=messages, registered_artifacts=[],
        invalid_args_history=[{"tool_call_id": "bad", "capability": "plot_data"}],
    )
    assert verdict.status == "EXECUTED_NO_OBJECT"


def test_a_second_attempt_that_succeeds_outranks_a_first_that_failed():
    messages = [
        _ai(("plot_data", "a")), _result("a", "[ERROR] Column not found"),
        _ai(("plot_data", "b")), _result("b", artifact=_chart_artifact()),
    ]
    assert _classify(messages=messages,
                     registered_artifacts=[PNG]).status == "SATISFIED"


# ── 20: evidence that contradicts itself ───────────────────────────────────

def test_an_artifact_nobody_in_this_turn_produced_suppresses_repair():
    """Nothing this turn drew anything, yet the working set gained a chart --
    a concurrent background turn on the same conversation, or accounting that
    lost a call. Repairing into that race would have the model draw while
    something else writes. Report it; do not act on it."""
    verdict = _classify(messages=[AIMessage(content="tamam")],
                        registered_artifacts=[OTHER_PNG, PNG],
                        baseline_artifacts=[OTHER_PNG])
    assert verdict.status == "MISSING_NO_ATTEMPT"
    assert verdict.anomaly is True
    assert verdict.repairable is False, "an inconsistent world is not a repair case"


def test_without_a_baseline_no_anomaly_is_ever_claimed():
    """The baseline is diagnostic only. Absent it, 'gained' is unknowable --
    and an unknown must not be reported as a contradiction."""
    verdict = _classify(messages=[AIMessage(content="tamam")], registered_artifacts=[PNG])
    assert verdict.status == "MISSING_NO_ATTEMPT"
    assert verdict.anomaly is False
    assert verdict.repairable is True


# ── the message walk itself ────────────────────────────────────────────────

def test_every_round_of_the_turn_is_read_not_only_the_last():
    """tool_accounting only ever needs the last round; the contract needs the
    whole turn, or a first-round attempt disappears from the record."""
    messages = [
        _ai(("plot_data", "a")), _result("a", "[ERROR] boom"),
        _ai(("csv_read", "x")), _result("x", "ok"),
        _ai(("plot_data", "b")), _result("b", artifact=_chart_artifact()),
    ]
    calls = turn_tool_calls(messages, CREATE)
    assert [c.call_id for c in calls] == ["a", "b"]
    assert [c.ok for c in calls] == [False, True]


def test_a_failed_call_contributes_no_artifacts_even_if_it_declared_some():
    """A tool can declare a path and then fail; the file may be half-written
    or already cleaned up. Only a successful call's declaration is evidence."""
    calls = turn_tool_calls(
        [_ai(("plot_data", "c1")),
         _result("c1", "[ERROR] render failed", artifact=_chart_artifact())],
        CREATE,
    )
    assert calls[0].artifacts == ()


def test_non_chart_artifact_kinds_are_ignored():
    """finance('export') declares a workbook alongside its chart; a report
    declares a PDF. A creation contract for `chart` must not be satisfied by
    a file of some other kind that happens to be in the same declaration."""
    workbook = [{"path": PNG, "kind": "workbook", "produced_by": "finance"}]
    verdict = _classify(messages=[_ai(("plot_data", "c1")),
                                  _result("c1", artifact=workbook)],
                        registered_artifacts=[PNG])
    assert verdict.status == "OUTPUT_EVIDENCE_MISMATCH"


def test_history_entries_for_other_tools_do_not_reclassify_a_chart_call():
    """gmail's rejected args in the same turn must not make the chart look
    like it was the one that failed validation."""
    verdict = _classify(
        messages=[_ai(("plot_data", "c1")), _result("c1", artifact=_chart_artifact())],
        registered_artifacts=[PNG],
        invalid_args_history=[{"tool_call_id": "g1", "capability": "gmail"}],
        preexecution_history=[{"tool_call_id": "g1", "capability": "gmail",
                               "outcome": "blocked_turn_limit"}],
    )
    assert verdict.status == "SATISFIED"


def test_malformed_history_entries_are_skipped_not_fatal():
    verdict = _classify(
        messages=[_ai(("plot_data", "c1")), _result("c1", "[BLOCKED: nope]")],
        preexecution_history=["junk", None, {"capability": "plot_data", "tool_call_id": "c1"}],
    )
    assert verdict.status == "MISSING_PREEXECUTION_BLOCK"


def test_a_relative_declared_path_resolves_against_the_process_cwd(isolated_cwd):
    """plot_data declares an absolute path today, but `canonical` must not
    silently treat a relative one as a different file from its absolute form."""
    (Path("out.png")).write_text("x", encoding="utf-8")
    absolute = str(Path("out.png").resolve())
    assert canonical("out.png") == canonical(absolute)
    assert canonical(absolute) == os.path.normcase(os.path.normpath(absolute))
