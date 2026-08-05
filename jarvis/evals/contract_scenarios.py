"""What the completion-contract gate measures, as data.

Separated from the harness script for the same reason `revision_scoring.py` is
separated from `revision_gate.py`: the script sets `JARVIS_HOME`, `os.chdir()`s
into a scratch home and imports the whole agent at module level, so pytest
cannot import it safely. The manifest is plain data plus one pure predicate, so
its coverage can be asserted without a model, a graph or a temp directory.

**Two populations, and conflating them is the trap this file exists to avoid.**

* `LIVE` scenarios are the A/B itself: a real qwen3:8b turn, run on both arms,
  scored on what the model actually did. Only three classes of behaviour can be
  provoked this way reliably -- a chart request, a non-chart control, and a tool
  that genuinely fails.
* `DETERMINISTIC` scenarios cover the rest of the contract's state space
  (invalid args, a pre-execution block, unreadable evidence, the wrong artifact
  kind, ...). A live model cannot be asked to produce those on demand -- you can
  only wait and hope -- so they are driven by fixtures and test doubles in
  `tests/test_completion_contract_harness.py`.

The pre-registered gate (`docs/eval/completion_contract_gate.md`) is explicit
that the second group "does not substitute for the live A/B corpus". Listing
both here, in one table, with the mechanism named per row, is what keeps a
deterministic pass from being reported as live evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Corpus = Literal["A", "B", "C", "D"]
Mechanism = Literal["live", "deterministic"]

#: The requirement the entry-point resolver is expected to produce. `None` means
#: "no contract" -- for corpus B that is the assertion, not an absence of one.
CHART_CREATE = {"kind": "chart", "operation": "create"}


def _chart_create_with_source(basename: str) -> dict:
    """CHART_CREATE plus the `source` binding required_outputs_for() now
    attaches when a query names an explicit file (Completion Contract Source
    Binding). Calls normalize_source_ref() itself rather than hand-writing
    the shape a second time -- a hand-written copy is exactly what drifted
    out of sync the first time this shape gained a field (is_explicit_path)
    and this test manifest did not, silently failing every A/C-corpus
    resolver-agreement check until caught."""
    from jarvis.execution.source_identity import normalize_source_ref

    return {**CHART_CREATE, "source": normalize_source_ref(basename)}


@dataclass(frozen=True)
class Scenario:
    id: str
    corpus: Corpus
    mechanism: Mechanism
    #: What the contract is being measured ON. Free text, shown in the report.
    behaviour: str
    #: The user turn, for live scenarios. Empty for deterministic ones, which
    #: drive the node directly rather than through a model.
    query: str = ""
    #: What `required_outputs_for()` must return. Checked before the model runs:
    #: if the resolver disagrees, the trial measures nothing about the contract.
    expected_requirement: dict | None = None
    #: The contract status this scenario is designed to produce. For live rows
    #: this is the EXPECTATION, not a guarantee -- the model may do something
    #: else, and that is a result, not a harness failure.
    expected_status: str = ""
    #: True only for MISSING_NO_ATTEMPT. Every other status is someone else's
    #: layer or a defect of ours; a repair offered anywhere else is a bug.
    repair_expected: bool = False
    #: Entry point. The gate requires BOTH arms use the same one per corpus.
    entry_point: Literal["chat", "chat_stream", "background_turn", "proactive_turn"] = "chat_stream"
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


SCENARIOS: tuple[Scenario, ...] = (
    # ── Corpus A: happy-path chart (LIVE, primary metric object_created) ─────
    Scenario(
        id="A1-explicit-chart",
        corpus="A", mechanism="live",
        behaviour="explicit chart request",
        query="Masaüstündeki satis.csv dosyasının aylık satış grafiğini çiz",
        expected_requirement=_chart_create_with_source("satis.csv"),
        expected_status="SATISFIED",
        notes="The 2x2 experiment's own target query, kept verbatim so the "
              "control arm is comparable with the 2026-08-03 numbers.",
        tags=("chart", "primary"),
    ),
    Scenario(
        id="A2-missing-no-attempt",
        corpus="A", mechanism="live",
        behaviour="MISSING_NO_ATTEMPT -- the class the contract exists for",
        query="satis.csv'nin grafiğini çiz",
        expected_requirement=_chart_create_with_source("satis.csv"),
        expected_status="MISSING_NO_ATTEMPT",
        repair_expected=True,
        notes="Cannot be forced: the model may well draw it. Scored on what "
              "happened, and MISSING_NO_ATTEMPT is the only repairable status.",
        tags=("chart", "repair"),
    ),
    Scenario(
        id="A3-regeneration",
        corpus="A", mechanism="live",
        behaviour="artifact already exists, user asks again",
        query="satis.csv'nin aylık satış grafiğini tekrar çiz",
        expected_requirement=_chart_create_with_source("satis.csv"),
        expected_status="SATISFIED",
        notes="A redraw with an unchanged spec bumps neither object_id nor "
              "version (register_chart skips patch(); record_artifact does not "
              "touch version) -- success must come from the NEW artifact path.",
        tags=("chart", "regeneration"),
    ),
    # NOTE: background_turn deliberately does NOT live in corpus A. The gate
    # requires both arms of a corpus to use one entry point, and mixing
    # chat_stream with background_turn inside the primary corpus would
    # contaminate object success, latency and the streaming metric at once.
    # Adding a fourth LIVE corpus would need a sample size the pre-registered
    # gate does not define -- and inventing one is exactly what the gate
    # forbids. So it is a scope assertion (D12), not a primary-metric row.

    # ── Corpus B: non-chart negative control (LIVE) ──────────────────────────
    Scenario(
        id="B1-analysis-only",
        corpus="B", mechanism="live",
        behaviour="control question wanting no artifact",
        query="satis.csv'yi analiz et",
        expected_requirement=None,
        expected_status="NOT_REQUIRED",
        notes="false_positive_contract must be 0: a contract here would hand a "
              "repair budget to a turn answering correctly.",
        tags=("control", "primary"),
    ),
    Scenario(
        id="B2-highest-value",
        corpus="B", mechanism="live",
        behaviour="data question, no output requested",
        query="satis.csv'de en yüksek satış hangi ay?",
        expected_requirement=None,
        expected_status="NOT_REQUIRED",
        tags=("control",),
    ),
    Scenario(
        id="B3-capability-question",
        corpus="B", mechanism="live",
        behaviour="capability question, not a request",
        query="grafik çizebiliyor musun?",
        expected_requirement=None,
        expected_status="NOT_REQUIRED",
        notes="No data reference -- the resolver's chart-expression AND "
              "data-reference rule must reject it.",
        tags=("control", "resolver"),
    ),
    Scenario(
        id="B4-proactive-outside",
        corpus="B", mechanism="deterministic",
        behaviour="proactive turn stays outside the contract",
        expected_requirement=None,
        expected_status="NOT_REQUIRED",
        entry_point="proactive_turn",
        notes="proactive_turn never writes the contract state fields, so the "
              "node reads NOT_REQUIRED whatever the mode. Asserted structurally "
              "rather than by running a monitor cycle.",
        tags=("control", "proactive"),
    ),

    # ── Corpus C: deterministic failure (LIVE, eligibility-gated) ────────────
    Scenario(
        id="C1-missing-file",
        corpus="C", mechanism="live",
        behaviour="MISSING_TOOL_FAILURE -- tool ran and honestly failed",
        query="yok_boyle_bir_dosya.csv dosyasının satış grafiğini çiz",
        expected_requirement=_chart_create_with_source("yok_boyle_bir_dosya.csv"),
        expected_status="MISSING_TOOL_FAILURE",
        notes="A non-existent file with structurally valid args. The gate "
              "forbids 'invalid column' as the trigger: fit_columns() repairs "
              "requested columns and the call can succeed.",
        tags=("failure", "primary"),
    ),
    Scenario(
        id="C2-corrupt-file",
        corpus="C", mechanism="live",
        behaviour="MISSING_TOOL_FAILURE via an unsupported/corrupt file",
        query="bozuk.csv dosyasının grafiğini çiz",
        expected_requirement=_chart_create_with_source("bozuk.csv"),
        expected_status="MISSING_TOOL_FAILURE",
        tags=("failure",),
    ),

    # ── Corpus D: contract state space not reachable from a live prompt ──────
    Scenario(
        id="D1-invalid-args",
        corpus="D", mechanism="deterministic",
        behaviour="MISSING_INVALID_ARGS stays with confirmation_node",
        expected_requirement=CHART_CREATE,
        expected_status="MISSING_INVALID_ARGS",
        notes="Must NOT route to a completion repair -- the args path already "
              "owns its own bounded retry.",
        tags=("state-space",),
    ),
    Scenario(
        id="D2-preexecution-block",
        corpus="D", mechanism="deterministic",
        behaviour="MISSING_PREEXECUTION_BLOCK, including the user's denial",
        expected_requirement=CHART_CREATE,
        expected_status="MISSING_PREEXECUTION_BLOCK",
        notes="A blocked call leaves the same failing ToolMessage stub a real "
              "error does; only the id-keyed preexecution_history tells them "
              "apart. A user denial must never be retried.",
        tags=("state-space",),
    ),
    Scenario(
        id="D3-executed-no-object",
        corpus="D", mechanism="deterministic",
        behaviour="EXECUTED_NO_OBJECT -- our postcondition defect",
        expected_requirement=CHART_CREATE,
        expected_status="EXECUTED_NO_OBJECT",
        notes="Tool declared an artifact the working set never kept. Audited, "
              "never re-prompted -- retrying would hide a registration bug.",
        tags=("state-space",),
    ),
    Scenario(
        id="D4-evidence-mismatch",
        corpus="D", mechanism="deterministic",
        behaviour="OUTPUT_EVIDENCE_MISMATCH -- success with no declaration",
        expected_requirement=CHART_CREATE,
        expected_status="OUTPUT_EVIDENCE_MISMATCH",
        tags=("state-space",),
    ),
    Scenario(
        id="D5-evidence-unavailable",
        corpus="D", mechanism="deterministic",
        behaviour="EVIDENCE_UNAVAILABLE -- the store could not be read",
        expected_requirement=CHART_CREATE,
        expected_status="EVIDENCE_UNAVAILABLE",
        notes="Never blame the model for our own SQLite, and never offer a "
              "repair for a chart that may already exist.",
        tags=("state-space",),
    ),
    Scenario(
        id="D6-wrong-artifact-kind",
        corpus="D", mechanism="deterministic",
        behaviour="wrong artifact kind is not success",
        expected_requirement=CHART_CREATE,
        expected_status="OUTPUT_EVIDENCE_MISMATCH",
        notes="finance('export') declares a workbook alongside its chart; a "
              "creation contract for `chart` must not be satisfied by a file "
              "of another kind in the same declaration.",
        tags=("state-space", "artifact-kind"),
    ),
    Scenario(
        id="D7-finance-embedded-chart",
        corpus="D", mechanism="deterministic",
        behaviour="finance('export') embedded chart is out of scope",
        expected_requirement=CHART_CREATE,
        expected_status="MISSING_NO_ATTEMPT",
        notes="Known, deliberate scope limit: finance registers no Working Set "
              "object, so the chart it draws cannot be revised. Pinned so the "
              "boundary is visible rather than discovered later.",
        tags=("state-space", "scope-limit"),
    ),
    Scenario(
        id="D8-streaming-no-leak",
        corpus="D", mechanism="deterministic",
        behaviour="a contracted streaming turn leaks no discarded draft",
        expected_requirement=CHART_CREATE,
        notes="A completion repair REPLACES the answer; voice swallows the "
              "__jarvis_final__ marker, so the buffer is the only fix.",
        tags=("streaming",),
    ),
    Scenario(
        id="D9-confirmation-resume",
        corpus="D", mechanism="deterministic",
        behaviour="confirmation resume keeps the terminal answer",
        expected_requirement=CHART_CREATE,
        notes="resume_and_stream must read state['response'] from the "
              "checkpoint, and must not hold back __jarvis_confirm__.",
        tags=("streaming", "confirmation"),
    ),
    Scenario(
        id="D10-repair-answer-survives",
        corpus="D", mechanism="deterministic",
        behaviour="terminal answer survives a repair",
        expected_requirement=CHART_CREATE,
        repair_expected=True,
        tags=("repair",),
    ),
    Scenario(
        id="D12-background-turn-inside",
        corpus="D", mechanism="deterministic",
        behaviour="background turn IS inside the contract",
        expected_requirement=CHART_CREATE,
        entry_point="background_turn",
        notes="'Run this in the background: draw the sales chart' is exactly "
              "the request the contract exists for -- background_turn is the "
              "USER's work running off-thread. Only proactive_turn (JARVIS's "
              "own monitor check) is structurally outside. Asserted on the "
              "wiring rather than by a live run: see the entry-point note "
              "above for why it cannot join corpus A.",
        tags=("background", "scope"),
    ),
    Scenario(
        id="D11-repair-toolset",
        corpus="D", mechanism="deterministic",
        behaviour="repair round sees only the safe creation subset",
        expected_requirement=CHART_CREATE,
        repair_expected=True,
        notes="Chart creators plus none/local_read only. Dispatch tools are "
              "dropped whatever their tool-level class says.",
        tags=("repair", "safety"),
    ),
)


#: Every contract status the classifier can return. Kept here so the coverage
#: assertion below cannot silently pass by forgetting one.
CONTRACT_STATUSES: frozenset[str] = frozenset({
    "NOT_REQUIRED", "SATISFIED", "MISSING_NO_ATTEMPT", "MISSING_INVALID_ARGS",
    "MISSING_PREEXECUTION_BLOCK", "MISSING_TOOL_FAILURE", "EXECUTED_NO_OBJECT",
    "OUTPUT_EVIDENCE_MISMATCH", "EVIDENCE_UNAVAILABLE",
})


def by_corpus(corpus: Corpus) -> tuple[Scenario, ...]:
    return tuple(s for s in SCENARIOS if s.corpus == corpus)


def live_scenarios() -> tuple[Scenario, ...]:
    return tuple(s for s in SCENARIOS if s.mechanism == "live")


def covered_statuses() -> frozenset[str]:
    return frozenset(s.expected_status for s in SCENARIOS if s.expected_status)
