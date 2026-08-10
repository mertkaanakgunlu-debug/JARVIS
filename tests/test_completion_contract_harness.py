"""The completion-contract gate's own guards.

Two jobs, and they are different:

* **The manifest covers what it claims to.** A scenario table that quietly
  stopped covering a contract status would make a pilot look complete while
  measuring less than it says.
* **Shadow really is a shadow, and enforce really is bounded.** Both are
  asserted here with fixtures and test doubles rather than a live model,
  because a live model cannot be asked to produce `MISSING_INVALID_ARGS` or an
  unreadable working set on demand -- you can only wait and hope.

The pre-registered gate (`docs/eval/completion_contract_gate.md`) is explicit
that this file "does not substitute for the live A/B corpus". These tests prove
the mechanism; the harness measures the behaviour.

`scripts/completion_contract_ab.py` is deliberately NOT imported: it sets
JARVIS_HOME, chdir()s into a scratch home and imports the whole agent at module
scope. The manifest lives in `jarvis/evals/contract_scenarios.py` precisely so
this file can import it safely -- the same split `revision_scoring.py` has from
`revision_gate.py`.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from jarvis.config import Settings
from jarvis.evals.contract_metrics import (
    honest_failure_retry_evidence,
    percentile,
    rate,
)
from jarvis.evals.contract_scenarios import (
    CONTRACT_STATUSES,
    SCENARIOS,
    by_corpus,
    covered_statuses,
    live_scenarios,
)
from jarvis.execution.output_contract import classify
from jarvis.graph.nodes import make_output_contract_node
from jarvis.nlu.output_intent import required_outputs_for
from jarvis.tool_registry import tools_producing

CHART = [{"kind": "chart", "operation": "create"}]
CONFIG = {"configurable": {"conversation_id": "conv-gate"}}
PNG = "C:/tmp/run-1/chart.png"


# ── the manifest covers the contract's whole state space ───────────────────

def test_every_contract_status_has_a_scenario():
    """A status with no scenario is a status nobody measures. The classifier's
    own vocabulary is the denominator, so adding a status without adding a
    scenario fails here rather than showing up as a quiet coverage hole."""
    missing = CONTRACT_STATUSES - covered_statuses()
    assert not missing, f"contract statuses with no scenario: {sorted(missing)}"


def test_scenario_ids_are_unique():
    ids = [s.id for s in SCENARIOS]
    assert len(ids) == len(set(ids))


def test_only_missing_no_attempt_expects_a_repair():
    """The whole point of separating the statuses: every other one is either
    someone else's layer, an honest failure, or a defect of ours."""
    for scenario in SCENARIOS:
        if scenario.repair_expected and scenario.expected_status:
            assert scenario.expected_status == "MISSING_NO_ATTEMPT", (
                f"{scenario.id} expects a repair for {scenario.expected_status}"
            )


def test_each_live_corpus_uses_one_entry_point_on_both_arms():
    """The gate requires it: a transport difference would contaminate object
    success, latency and the streaming-duplicate metric at once."""
    for corpus in ("A", "B", "C"):
        entry_points = {s.entry_point for s in by_corpus(corpus) if s.mechanism == "live"}
        assert len(entry_points) <= 1, f"corpus {corpus} mixes entry points: {entry_points}"


def test_live_scenarios_all_carry_a_query():
    for scenario in live_scenarios():
        assert scenario.query, f"{scenario.id} is live but has no query"


@pytest.mark.parametrize("scenario", [s for s in SCENARIOS if s.query],
                         ids=lambda s: s.id)
def test_the_resolver_agrees_with_each_scenarios_expectation(scenario):
    """If the deterministic resolver disagrees with the manifest, the trial
    measures nothing about the contract -- it measures the resolver. Checked
    before any model runs, which is the only useful time."""
    resolved = required_outputs_for(scenario.query)
    expected = [scenario.expected_requirement] if scenario.expected_requirement else []
    assert resolved == expected, (
        f"{scenario.id}: resolver returned {resolved}, manifest expects {expected}"
    )


def test_corpus_c_avoids_the_forbidden_failure_trigger():
    """The gate forbids "invalid column" as the failure trigger: plot_data
    repairs requested columns via fit_columns() and the call can SUCCEED, so
    the corpus would silently stop producing eligible trials."""
    for scenario in by_corpus("C"):
        lowered = scenario.query.lower()
        assert "kolon" not in lowered and "column" not in lowered, (
            f"{scenario.id} triggers failure via a column name"
        )


# ── shadow parity, per verdict ─────────────────────────────────────────────

def _ai(*calls):
    return AIMessage(content="", tool_calls=[
        {"name": n, "args": {}, "id": i} for n, i in calls
    ])


def _drew(path=PNG):
    return [_ai(("plot_data", "c1")),
            ToolMessage(content="ok", tool_call_id="c1",
                        artifact=[{"path": path, "kind": "chart"}])]


class _Store:
    def __init__(self, artifacts=()):
        self._artifacts = tuple(artifacts)

    def active(self, conversation_id, kind=""):
        return MagicMock(source_artifacts=self._artifacts) if self._artifacts else None

    def list(self, conversation_id, kind=""):
        if not self._artifacts:
            return []
        obj = MagicMock(source_artifacts=self._artifacts)
        obj.spec = {}  # no source binding exercised by this file's scenarios
        return [obj]


def _state(messages, **overrides):
    state = {
        "messages": messages, "user_query": "satis.csv grafiğini çiz",
        "required_outputs": CHART, "response": "taslak cevap",
        "tool_rounds": 0, "tool_calls_attempted": 0,
        "repair_attempts_total": 0, "repair_reason": "",
        "args_repair_attempted": False,
    }
    state.update(overrides)
    return state


_VERDICT_FIXTURES = [
    ("MISSING_NO_ATTEMPT", [HumanMessage(content="çiz"), AIMessage(content="hangi format?")], ()),
    ("SATISFIED", _drew(), (PNG,)),
    ("EXECUTED_NO_OBJECT", _drew(), ()),
    ("MISSING_TOOL_FAILURE",
     [_ai(("plot_data", "c1")), ToolMessage(content="[ERROR] no file", tool_call_id="c1")], ()),
    ("OUTPUT_EVIDENCE_MISMATCH",
     [_ai(("plot_data", "c1")), ToolMessage(content="ok", tool_call_id="c1")], (PNG,)),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("expected,messages,artifacts", _VERDICT_FIXTURES,
                         ids=[f[0] for f in _VERDICT_FIXTURES])
async def test_shadow_classifies_without_touching_the_turn(
    rollout_metrics_file, expected, messages, artifacts,
):
    """Stage 6. Parametric on purpose: a mode that edits the answer for ONE
    verdict is not a shadow, and a single happy-path assertion would not catch
    it. The only key shadow may return is the router's own transient signal --
    no message, no response, no repair budget, no route back to the agent."""
    node = make_output_contract_node(
        Settings(_env_file=None, required_outputs_mode="shadow"), _Store(artifacts)
    )
    state = _state(messages)

    out = await node(state, CONFIG)

    assert out == {"output_contract_action": "continue"}
    rows = [json.loads(x) for x in
            Path(rollout_metrics_file).read_text(encoding="utf-8").splitlines()]
    decisions = [r for r in rows if r["event"] == "output_contract_decision"]
    assert [r["status"] for r in decisions] == [expected], "telemetry only, and correct"


@pytest.mark.asyncio
async def test_shadow_adds_no_tool_call_and_no_repair(rollout_metrics_file):
    node = make_output_contract_node(
        Settings(_env_file=None, required_outputs_mode="shadow"), _Store()
    )
    before = _state([HumanMessage(content="çiz"), AIMessage(content="hangi format?")])
    snapshot = dict(before)

    out = await node(before, CONFIG)

    assert "messages" not in out and "response" not in out
    assert "repair_attempts_total" not in out
    assert before == snapshot, "shadow must not mutate the state it was handed"


# ── enforce: bounded, evidence-backed, and never retrying the wrong class ───

@pytest.mark.asyncio
@pytest.mark.parametrize("status,messages,artifacts,history", [
    ("MISSING_TOOL_FAILURE",
     [_ai(("plot_data", "c1")), ToolMessage(content="[ERROR] no file", tool_call_id="c1")],
     (), {}),
    ("MISSING_INVALID_ARGS",
     [_ai(("plot_data", "c1")), ToolMessage(content="[INVALID_ARGS:path] req", tool_call_id="c1")],
     (), {"invalid_args_history": [{"tool_call_id": "c1", "capability": "plot_data"}]}),
    ("MISSING_PREEXECUTION_BLOCK",
     [_ai(("plot_data", "c1")), ToolMessage(content="[BLOCKED: denied]", tool_call_id="c1")],
     (), {"preexecution_history": [{"tool_call_id": "c1", "capability": "plot_data",
                                    "outcome": "user_denied"}]}),
    ("EXECUTED_NO_OBJECT", _drew(), (), {}),
])
async def test_enforce_never_retries_a_class_it_must_not(
    rollout_metrics_file, status, messages, artifacts, history,
):
    """Stage 7. An honest tool error, a rejected batch, the user's own denial
    and our own registration defect are four different things -- and none of
    them is a model that failed to try."""
    node = make_output_contract_node(
        Settings(_env_file=None, required_outputs_mode="enforce"), _Store(artifacts)
    )

    out = await node(_state(messages, **history), CONFIG)

    assert out["output_contract_action"] == "continue", f"{status} must not repair"
    assert out["response_origin"] == "output_contract"
    assert "repair_attempts_total" not in out


@pytest.mark.asyncio
async def test_enforce_gives_at_most_one_repair(rollout_metrics_file):
    node = make_output_contract_node(
        Settings(_env_file=None, required_outputs_mode="enforce"), _Store()
    )
    idle = [HumanMessage(content="çiz"), AIMessage(content="hangi format?")]

    first = await node(_state(idle), CONFIG)
    assert first["output_contract_action"] == "repair"
    assert first["repair_attempts_total"] == 1

    second = await node(_state(idle, **{k: v for k, v in first.items() if k != "messages"}),
                        CONFIG)
    assert second["output_contract_action"] == "continue", "a second repair is a loop"


@pytest.mark.asyncio
async def test_enforce_needs_a_real_artifact_not_just_a_working_set_object(
    rollout_metrics_file,
):
    """An object with no artifact this turn produced is last turn's chart."""
    node = make_output_contract_node(
        Settings(_env_file=None, required_outputs_mode="enforce"), _Store((PNG,))
    )
    out = await node(_state([HumanMessage(content="çiz"), AIMessage(content="tamam")]), CONFIG)
    assert out["output_contract_action"] == "repair", "no chart was produced this turn"


def test_a_wrong_artifact_kind_is_not_success():
    """finance('export') declares a workbook beside its chart; a creation
    contract for `chart` must not be satisfied by another kind."""
    verdict = classify(
        required=CHART, capabilities=tools_producing("chart", "create"),
        messages=[_ai(("plot_data", "c1")),
                  ToolMessage(content="ok", tool_call_id="c1",
                              artifact=[{"path": PNG, "kind": "workbook"}])],
        registered_artifacts=[PNG],
    )
    assert verdict.status == "OUTPUT_EVIDENCE_MISMATCH"
    assert verdict.repairable is False


def test_the_artifact_source_is_the_message_not_the_ledger():
    """The canonical source has to be mode-independent. safe_tools attaches
    ToolMessage.artifact on every call; tool_accounting only parses it when
    execution_contract_mode != "off", and ledger rows carry neither the
    artifact list nor a tool_call_id -- so reading from there would tie this
    measurement to an unrelated flag."""
    import inspect

    from jarvis.execution import output_contract

    src = inspect.getsource(output_contract)
    assert 'getattr(answer, "artifact", None)' in src
    assert "execution_envelopes" not in src, (
        "the classifier must not read envelopes -- they are mode-gated"
    )


# ── the counting itself (a defect the first smoke run exposed) ─────────────

def test_a_dict_valued_field_counts_by_its_flag_not_by_being_a_dict():
    """THE regression. `source_mutation` is a dict, so `if row.get(key)` is
    true for every row -- the first smoke run printed "source mutation 3/3"
    on both arms while the gate clause, reading `.get("any")`, printed 0.

    Two numbers from one field disagreeing on screen is how a real fixture
    mutation gets dismissed as "that counter is always full"."""
    clean = {"source_mutation": {"changed": [], "deleted": [], "any": False}}
    dirty = {"source_mutation": {"changed": ["satis.csv"], "deleted": [], "any": True}}

    assert rate([clean, clean, clean], "source_mutation") == (0, 3)
    assert rate([clean, dirty, clean], "source_mutation") == (1, 3)


def test_plain_boolean_fields_still_count_normally():
    rows = [{"object_created": True}, {"object_created": False}, {}]
    assert rate(rows, "object_created") == (1, 3)


def test_a_percentile_with_no_data_is_not_an_exception():
    """A clause with no data must read UNMEASURED, not take down the run that
    produced every other number."""
    assert percentile([], 0.9) == 0.0


def test_percentiles_use_nearest_rank():
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.5) == 2.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.9) == 4.0


# -- Finding 2: corrected diagnostic, historical gate unchanged ----------------

def _retry_evidence(
    ledger, *, before=("failed-1",), invalid=(), blocked=(), capabilities=("plot_data",),
):
    return honest_failure_retry_evidence(
        ledger=ledger,
        repair_boundaries=[{"after_tool_call_ids": list(before)}],
        capabilities=capabilities,
        invalid_args_history=[{"tool_call_id": call_id} for call_id in invalid],
        preexecution_history=[{"tool_call_id": call_id} for call_id in blocked],
    )


def test_corrected_diagnostic_sees_failure_repair_then_success_blind_spot():
    """A later artifact makes the historical final-state row ineligible, but
    cannot erase the earlier failed call from the temporal diagnostic."""
    historical_row = {
        "chart_attempted": True,
        "chart_executed": True,
        "repair_attempted": True,
    }
    legacy_eligible = (
        historical_row["chart_attempted"] and not historical_row["chart_executed"]
    )
    ledger = [
        {"tool": "plot_data", "tool_call_id": "failed-1", "ok": False},
        {"tool": "plot_data", "tool_call_id": "success-2", "ok": True},
    ]

    assert legacy_eligible is False
    assert _retry_evidence(ledger) == [{
        "tool": "plot_data",
        "failed_tool_call_id": "failed-1",
        "retry_tool_call_id": "success-2",
        "retry_ok": True,
        "repair_index": 1,
    }]


@pytest.mark.parametrize("excluded_kind", ["invalid", "blocked", "user_denied"])
def test_corrected_diagnostic_excludes_calls_that_never_executed(excluded_kind):
    ledger_like_fixture = [
        {"tool": "plot_data", "tool_call_id": "failed-1", "ok": False},
        {"tool": "plot_data", "tool_call_id": "success-2", "ok": True},
    ]
    invalid = ("failed-1",) if excluded_kind == "invalid" else ()
    blocked = ("failed-1",) if excluded_kind in {"blocked", "user_denied"} else ()

    assert _retry_evidence(
        ledger_like_fixture, invalid=invalid, blocked=blocked,
    ) == []


def test_corrected_diagnostic_does_not_relabel_missing_no_attempt_repair():
    assert _retry_evidence(
        [{"tool": "plot_data", "tool_call_id": "success-1", "ok": True}],
        before=(),
    ) == []


def test_corrected_diagnostic_keys_same_named_calls_by_tool_call_id():
    ledger = [
        {"tool": "plot_data", "tool_call_id": "blocked-1", "ok": False},
        {"tool": "plot_data", "tool_call_id": "failed-2", "ok": False},
        {"tool": "plot_data", "tool_call_id": "success-3", "ok": True},
    ]

    evidence = _retry_evidence(
        ledger, before=("blocked-1", "failed-2"), blocked=("blocked-1",),
    )

    assert [(e["failed_tool_call_id"], e["retry_tool_call_id"]) for e in evidence] == [
        ("failed-2", "success-3"),
    ]


def test_corrected_diagnostic_excludes_unknown_outcomes():
    ledger = [
        {"tool": "plot_data", "tool_call_id": "failed-1", "ok": False,
         "outcome": "unknown"},
        {"tool": "plot_data", "tool_call_id": "success-2", "ok": True},
    ]

    assert _retry_evidence(ledger) == []
