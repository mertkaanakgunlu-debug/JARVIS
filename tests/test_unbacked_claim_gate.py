"""Post-MVP Faz 1 -- the unbacked-claim gate, detector and wiring.

Split in two, deliberately. The first half is a truth table for
evidence.detect_unbacked_claims(): every row is a sentence a model plausibly
emits, and the FALSE rows matter more than the TRUE ones -- this gate can
replace a user-facing answer, so a false positive is the expensive failure.
The two rows marked from the plan (*"Bunu 3 adimda yapabiliriz"*, *"89
islemin 45'i gelir"*) are the external review's own counter-examples against
number-scanning; the inline-list row is a false positive this detector
actually produced on its first run and was tightened to fix.

The second half drives the TERMINAL verification node through the mode
ladder, because a correct detector wired in wrongly is still a broken
feature: shadow must observe without touching a single character of the
answer, and enforce must run exactly ONE repair round -- never a loop --
before falling back to an honest report.

The gate is a terminal node rather than part of compose_node, and the
test_every_path_to_end_passes_verify case at the bottom is why: the graph
finishes a plain conversational turn as agent -> critic -> END, which never
reaches compose. A gate living there was blind to its own headline case
("claimed a file, called no tool"), which is most often exactly that kind
of turn. That was found by reading the live graph wiring, not by any test
here -- hence the wiring assertion.
"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import jarvis.providers as providers
from jarvis.config import Settings
from jarvis.execution.envelope import ExecutionEnvelope
from jarvis.execution.evidence import (
    ArtifactEvidence,
    EvidenceSet,
    build_evidence_set,
    detect_unbacked_claims,
    file_references,
)
from jarvis.execution.summary import build_verified_summary
from jarvis.graph.nodes import make_verification_node

NO_EVIDENCE = EvidenceSet()


# ── detector truth table ───────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    # the recorded live incident: a PNG reported at a path, nothing ran
    "Grafiği oluşturdum ve C:/Users/mertk/Desktop/Jarvis/data/plots/chart.png konumuna kaydettim.",
    "Rapor hazır, rapor_2026.pdf olarak kaydettim efendim.",
    "Excel dosyasını oluşturdum: cashflow_2026-07.xlsx",
    "I've saved the chart to C:/out/plot.png.",
    "The workbook has been exported to summary.xlsx.",
    # no file named, but a side effect asserted in a turn where nothing ran
    "Grafiği oluşturdum ve kaydettim efendim.",
    "Maili gönderdim.",
])
def test_fires_on_an_unbacked_claim(text):
    assert detect_unbacked_claims(text, NO_EVIDENCE).unbacked is True


@pytest.mark.parametrize("text", [
    # the external review's own false-positive counter-examples
    "Bunu 3 adımda yapabiliriz: önce veriyi okuruz, sonra grafik çizeriz.",
    "89 işlemin 45'i gelir, 44'ü giderdir.",
    # a real false positive this detector produced before it was tightened:
    # composing something inline is not a side effect
    "Sizin için bir liste oluşturdum: 1) süt 2) ekmek 3) yumurta",
    "İşte özet tablo:\n| ay | tutar |\n|---|---|\n| 7 | 100 |",
    # offers and future forms are not claims
    "Excel dosyası (.xlsx) olarak hazırlayabilirim, ister misiniz?",
    "İsterseniz grafiği oluşturup rapor.pdf olarak kaydedeyim.",
    "Yarın bir rapor hazırlayacağım.",
    # a purpose clause, not a completion
    "Grafik oluşturmak için verileri hazırladım.",
    # an honest failure is the model behaving correctly
    "Dosyayı oluşturamadım, bir hata oluştu.",
    "I could not create chart.png -- the data file was not found.",
    # A MIXED answer -- claims one thing, admits another failed -- is the
    # only shape that actually exercises the negation guard, and mutation
    # testing is how that was discovered: the two rows above never reach it
    # (they make no completion claim at all, so the check returns earlier).
    # Hedging correctly must never be punished, so the whole response passes.
    "Grafiği oluşturdum ve chart.png olarak kaydettim, ancak Excel dosyası oluşturulamadı.",
    "I saved report.pdf but the email could not be sent.",
    # a past-tense reference to earlier work, not a claim about this turn
    "Daha önce oluşturduğum grafiği güncelleyebilirim.",
    "",
    "   ",
])
def test_stays_silent_when_it_cannot_prove_a_contradiction(text):
    assert detect_unbacked_claims(text, NO_EVIDENCE).unbacked is False


def test_a_common_turkish_reassurance_does_not_disable_the_gate():
    """"sorun yok" must not read as a failure admission -- a bare \\byok\\b in
    the negation list would let one throwaway phrase switch the whole gate
    off, which is a recall hole wide enough for the original hallucination."""
    verdict = detect_unbacked_claims(
        "Grafiği oluşturdum ve uydurma.png olarak kaydettim. Başka bir sorun yok.",
        NO_EVIDENCE,
    )
    assert verdict.unbacked is True


def test_an_explicit_no_output_admission_still_suppresses_it():
    assert detect_unbacked_claims(
        "İşlemi tamamladım ama çıktı yok, dosya üretilmedi.", NO_EVIDENCE
    ).unbacked is False


def test_a_claim_backed_by_a_declared_artifact_is_clean(tmp_path):
    real = tmp_path / "chart.png"
    real.write_bytes(b"png")
    evidence = EvidenceSet(artifacts=[ArtifactEvidence(path=str(real), exists=True)])
    text = f"Grafiği oluşturdum ve {real} konumuna kaydettim."
    assert detect_unbacked_claims(text, evidence).unbacked is False


def test_a_shortened_basename_still_counts_as_backed(tmp_path):
    """A response legitimately says "chart.png", not the full run directory."""
    real = tmp_path / "run-abc" / "chart.png"
    real.parent.mkdir()
    real.write_bytes(b"png")
    evidence = EvidenceSet(artifacts=[ArtifactEvidence(path=str(real), exists=True)])
    assert detect_unbacked_claims("Grafiği kaydettim: chart.png", evidence).unbacked is False


def test_an_invented_second_file_is_caught_even_when_a_tool_did_run(tmp_path):
    """The high-value case: one real artifact, one fabricated alongside it."""
    real = tmp_path / "chart.png"
    real.write_bytes(b"png")
    evidence = EvidenceSet(artifacts=[ArtifactEvidence(path=str(real), exists=True)])
    verdict = detect_unbacked_claims(
        f"Grafiği {real} olarak kaydettim, özeti de ozet_raporu.pdf dosyasına yazdım.",
        evidence,
    )
    assert verdict.unbacked is True
    assert any("ozet_raporu.pdf" in f for f in verdict.unbacked_files)


def test_an_existing_file_is_never_called_a_lie(tmp_path, monkeypatch):
    """If the named file really is on disk we do not know the sentence is
    false -- the user may be asking about something written last week."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "eski.xlsx").write_bytes(b"x")
    verdict = detect_unbacked_claims("Dosyayı kaydettim: eski.xlsx", NO_EVIDENCE)
    assert verdict.unbacked is False


def test_a_declared_but_absent_artifact_does_not_back_a_claim(tmp_path):
    """exists=False means the tool declared it and the disk disagreed --
    that is the opposite of corroboration."""
    ghost = tmp_path / "ghost.png"
    evidence = EvidenceSet(artifacts=[ArtifactEvidence(path=str(ghost), exists=False)])
    verdict = detect_unbacked_claims(f"Grafiği {ghost} olarak kaydettim.", evidence)
    assert verdict.unbacked is True


def test_a_side_effect_claim_is_not_flagged_when_some_tool_ran():
    """With operations recorded, the zero-operation branch must stay quiet --
    judging whether THAT operation matches the sentence is summary.py's job,
    not this detector's."""
    evidence = EvidenceSet(
        operations=build_evidence_set(
            build_verified_summary([
                ExecutionEnvelope(
                    execution_id="a", capability="gmail_send", status="success",
                    inputs_digest="d",
                ).model_dump()
            ])
        ).operations
    )
    assert detect_unbacked_claims("Maili gönderdim.", evidence).unbacked is False


# ── file reference extraction ──────────────────────────────────────────────

def test_file_references_finds_paths_and_bare_names():
    refs = file_references(
        "C:/out/a.png ve ./sub/b.xlsx ve c.pdf üretildi, ayrıca /var/tmp/d.csv"
    )
    assert "C:/out/a.png" in refs
    assert any(r.endswith("b.xlsx") for r in refs)
    assert "c.pdf" in refs
    assert any(r.endswith("d.csv") for r in refs)


def test_a_bare_extension_is_not_a_file_reference():
    assert file_references("Excel (.xlsx) veya PDF (.pdf) olabilir") == []


def test_file_references_deduplicates():
    assert len(file_references("chart.png ve yine chart.png")) == 1


# ── evidence construction ──────────────────────────────────────────────────

def test_build_evidence_set_marks_existence(tmp_path):
    present = tmp_path / "there.png"
    present.write_bytes(b"png")
    missing = tmp_path / "gone.png"
    envelopes = [
        ExecutionEnvelope(
            execution_id="a", capability="plot_data", status="success",
            inputs_digest="d", artifacts=[str(present), str(missing)],
        ).model_dump()
    ]
    evidence = build_evidence_set(build_verified_summary(envelopes), envelopes)
    by_path = {a.path: a.exists for a in evidence.artifacts}
    assert by_path[str(present)] is True
    assert by_path[str(missing)] is False
    assert evidence.verified_artifact_paths == [str(present)]


def test_build_evidence_set_tolerates_junk():
    evidence = build_evidence_set(None, ["not-a-dict", {"capability": "x"}, {}])
    assert evidence.artifacts == []
    assert evidence.operations == []
    assert evidence.facts == {}, "facts stays empty until a tool emits structured facts"


# ── the TERMINAL verification node, across the mode ladder ─────────────────

class _ScriptedLLM:
    """Returns each scripted reply in turn; repeats the last one forever, so a
    test asserting "exactly one repair round" fails loudly on a loop rather
    than hanging. The verification node only ever calls an LLM for a repair,
    so len(log) IS the repair-round count."""

    def __init__(self, log: list, replies: list[str]):
        self._log = log
        self._replies = replies

    async def ainvoke(self, messages, config=None):
        self._log.append({"messages": list(messages), "config": config})
        idx = min(len(self._log) - 1, len(self._replies) - 1)
        return AIMessage(content=self._replies[idx])


def _patch_llm(monkeypatch, *replies: str) -> list:
    log: list = []
    monkeypatch.setattr(
        providers, "get_llm", lambda role, settings=None, **k: _ScriptedLLM(log, list(replies))
    )
    return log


HALLUCINATION = "Grafiği oluşturdum ve C:/nope/uydurma_chart.png konumuna kaydettim."
HONEST = "Grafiği çizemedim çünkü hangi veriyi kullanacağımı bilmiyorum."


def _state(answer=HALLUCINATION, envelopes=None, query="grafiği çiz ve kaydet", set_response=True):
    """The verification node runs AFTER the answer exists -- it receives one,
    it does not produce one. set_response=False exercises the
    agent -> critic -> END path, where nothing ever populates
    state["response"] and the answer is only the trailing AIMessage."""
    state = {
        "messages": [HumanMessage(content=query), AIMessage(content=answer)],
        "use_pro_agent": False,
        "execution_envelopes": envelopes or [],
    }
    if set_response:
        state["response"] = answer
    return state


def _node(mode: str):
    return make_verification_node(settings=Settings(_env_file=None, execution_contract_mode=mode))


def _gate_rows(path):
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    return [r for r in rows if r["event"] == "claim_gate"]


async def test_off_mode_is_a_true_noop(monkeypatch, rollout_metrics_file):
    log = _patch_llm(monkeypatch, HONEST)
    assert await _node("off")(_state()) == {}
    assert log == [], "off must not spend a model call"
    assert not rollout_metrics_file.exists(), "off writes no metrics at all"


async def test_shadow_detects_but_never_edits_the_answer(monkeypatch, rollout_metrics_file):
    log = _patch_llm(monkeypatch, HONEST)
    audited: list = []
    monkeypatch.setattr("jarvis.audit_log.record", lambda e, **f: audited.append((e, f)))

    out = await _node("shadow")(_state())

    assert out == {}, "shadow must not touch the user's answer"
    assert log == [], "shadow must not spend a second model call"
    assert any(e == "unbacked_claim" for e, _ in audited)
    gate = _gate_rows(rollout_metrics_file)
    assert gate and gate[0]["fired"] is True and gate[0]["blocked"] is False


async def test_enforce_repairs_once_and_uses_the_clean_rewrite(monkeypatch, rollout_metrics_file):
    log = _patch_llm(monkeypatch, HONEST)
    out = await _node("enforce_reversible")(_state())

    assert out["response"] == HONEST
    assert len(log) == 1, "exactly one repair round"
    assert any(
        "System verification" in getattr(m, "content", "") for m in log[0]["messages"]
    ), "the repair round must carry the correction"
    gate = _gate_rows(rollout_metrics_file)[0]
    assert gate["repaired"] is True and gate["blocked"] is False


async def test_enforce_falls_back_to_an_honest_report_when_repair_also_lies(
    monkeypatch, rollout_metrics_file
):
    log = _patch_llm(monkeypatch, HALLUCINATION)
    out = await _node("enforce_reversible")(_state(query="bana bir grafik çiz efendim"))

    assert len(log) == 1, "bounded: one repair round, never a loop"
    assert "uydurma_chart.png" not in out["response"]
    assert out["response"].startswith("Efendim"), "Turkish query -> Turkish report"
    gate = _gate_rows(rollout_metrics_file)[0]
    assert gate["blocked"] is True and gate["repaired"] is False


async def test_a_timed_out_repair_falls_back_rather_than_passing_the_claim(
    monkeypatch, rollout_metrics_file
):
    """A repair that never comes back must not leave the fabrication standing."""
    import asyncio as _asyncio

    class _Hanging:
        async def ainvoke(self, messages, config=None):
            raise _asyncio.TimeoutError()

    monkeypatch.setattr(providers, "get_llm", lambda role, settings=None, **k: _Hanging())
    out = await _node("enforce_reversible")(_state())
    assert "uydurma_chart.png" not in out["response"]
    assert _gate_rows(rollout_metrics_file)[0]["blocked"] is True


async def test_the_honest_report_follows_the_users_language(monkeypatch):
    _patch_llm(monkeypatch, HALLUCINATION)
    out = await _node("enforce_reversible")(_state(query="draw me a chart of the sales data"))
    assert not out["response"].startswith("Efendim")
    assert "could not produce the file" in out["response"]


async def test_a_clean_answer_costs_no_model_call_at_all(monkeypatch):
    log = _patch_llm(monkeypatch, HONEST)
    assert await _node("enforce_reversible")(_state(answer=HONEST)) == {}
    assert log == [], "the gate is free when the answer is clean"


async def test_a_backed_claim_passes_untouched_in_enforce(monkeypatch, tmp_path):
    real = tmp_path / "chart.png"
    real.write_bytes(b"png")
    envelopes = [
        ExecutionEnvelope(
            execution_id="a", capability="plot_data", status="success",
            inputs_digest="d", artifacts=[str(real)],
        ).model_dump()
    ]
    log = _patch_llm(monkeypatch, HONEST)
    answer = f"Grafiği oluşturdum ve {real} konumuna kaydettim efendim."
    assert await _node("enforce_reversible")(_state(answer=answer, envelopes=envelopes)) == {}
    assert log == []


async def test_a_zero_tool_turn_reaches_the_gate(monkeypatch, rollout_metrics_file):
    """The class this phase exists for: no tool ran, so there are no
    envelopes, and every operation-based check is vacuously satisfied."""
    _patch_llm(monkeypatch, HALLUCINATION)
    out = await _node("enforce_reversible")(_state(envelopes=[]))
    assert "uydurma_chart.png" not in out["response"]


async def test_the_answer_is_read_from_the_last_message_when_response_is_unset(
    monkeypatch, rollout_metrics_file
):
    """agent -> critic -> END never populates state["response"]; reading only
    that key would make the gate silently no-op on exactly the turn shape it
    was built for."""
    _patch_llm(monkeypatch, HONEST)
    out = await _node("enforce_reversible")(_state(set_response=False))
    assert out["response"] == HONEST
    assert _gate_rows(rollout_metrics_file)[0]["fired"] is True


async def test_an_empty_turn_is_skipped_without_a_metric_row(monkeypatch, rollout_metrics_file):
    _patch_llm(monkeypatch, HONEST)
    assert await _node("shadow")({"messages": [], "execution_envelopes": []}) == {}
    assert not rollout_metrics_file.exists()


async def test_verification_rows_are_recorded_per_operation(monkeypatch, rollout_metrics_file):
    envelopes = [
        ExecutionEnvelope(
            execution_id="a", capability="plot_data", status="success",
            inputs_digest="d", artifacts=["C:/x/a.png"],
        ).model_dump(),
        ExecutionEnvelope(
            execution_id="b", capability="web_search", status="success", inputs_digest="d",
        ).model_dump(),
    ]
    _patch_llm(monkeypatch, HONEST)
    await _node("shadow")(_state(answer=HONEST, envelopes=envelopes))

    rows = [json.loads(x) for x in rollout_metrics_file.read_text(encoding="utf-8").splitlines()]
    verifications = {r["capability"]: r for r in rows if r["event"] == "verification"}
    assert verifications["plot_data"]["artifacts_declared"] == 1
    assert verifications["web_search"]["artifacts_declared"] == 0


async def test_exactly_one_gate_row_per_turn(monkeypatch, rollout_metrics_file):
    """Double-counting would corrupt the promotion metric as surely as
    under-counting -- it is why the gate is in ONE terminal node and not
    also in compose."""
    _patch_llm(monkeypatch, HONEST)
    await _node("shadow")(_state())
    assert len(_gate_rows(rollout_metrics_file)) == 1


# ── graph wiring: the gate is only as good as the paths it sits on ─────────

@pytest.mark.parametrize("required_outputs_mode", ["off", "shadow", "enforce"])
def test_every_path_to_end_passes_verify(required_outputs_mode, tmp_path):
    """Found by reading the live graph, not by a test: compose is NOT on
    every path to END, so a gate inside it never saw a plain conversational
    turn -- the single most likely shape of "claimed a file, called no
    tool".

    Asserted against the COMPILED edge map rather than build_graph's source
    text, and across every rollout mode. The source check this replaced
    matched two literal strings, so Post-MVP Faz 6 broke it simply by naming
    the destination `terminal_target` -- while the property itself still
    held. A wiring invariant should fail when the WIRING changes, not when
    the spelling does; and the version that reads the real graph also proves
    something the string version could not, namely that inserting
    output_contract ahead of verify did not open a second route to END.
    """
    from unittest.mock import MagicMock

    from jarvis.graph.graph import build_graph

    graph = build_graph(
        Settings(_env_file=None, required_outputs_mode=required_outputs_mode),
        tmp_path, MagicMock(),
    ).get_graph()

    reaches_end = {e.source for e in graph.edges if e.target == "__end__"}
    assert reaches_end == {"verify"}, (
        f"{sorted(reaches_end - {'verify'})} can finish a turn without the gate"
    )

    # ...and the nodes that DECIDE a turn is over still route into the chain
    # that ends at verify, rather than to some node that merely happens to.
    terminal = "output_contract" if required_outputs_mode != "off" else "verify"
    ends_at = {(e.source, e.target) for e in graph.edges if str(e.data or "") == "__end__"}
    assert ("critic", terminal) in ends_at
    assert ("confirmation", terminal) in ends_at


async def test_the_repair_round_is_tagged_for_the_stream_filter(monkeypatch):
    """Defence in depth: streaming.py filters by node name and "verify" is
    not in its allow-list, but this logic already lived inside compose once,
    where the node filter could NOT separate the discarded draft from its
    replacement. The tag means moving it again cannot silently reintroduce
    spliced output."""
    from jarvis.graph.nodes import REPAIR_STREAM_TAG

    log = _patch_llm(monkeypatch, HONEST)
    await _node("enforce_reversible")(_state())
    assert REPAIR_STREAM_TAG in log[0]["config"]["tags"]


def test_the_stream_filter_drops_repair_chunks():
    import asyncio

    from langchain_core.messages import AIMessageChunk

    from jarvis.graph.nodes import REPAIR_STREAM_TAG
    from jarvis.graph.streaming import graph_stream_to_text

    class _Graph:
        async def astream(self, state, config, stream_mode=None):
            yield AIMessageChunk(content="draft"), {
                "langgraph_node": "compose", "langgraph_step": 1, "tags": [],
            }
            yield AIMessageChunk(content="REPAIR"), {
                "langgraph_node": "compose", "langgraph_step": 1,
                "tags": [REPAIR_STREAM_TAG],
            }

    async def collect():
        return "".join([c async for c in graph_stream_to_text(_Graph(), {}, {})])

    assert asyncio.run(collect()) == "draft"


async def test_the_gate_ignores_the_system_authored_status_block(
    monkeypatch, rollout_metrics_file, tmp_path
):
    """compose_node appends its own code-authored block in enforce mode, and
    for a verification-FAILED operation that block quotes a path which is
    deliberately not on disk. Judged as model output it reads as a fabricated
    file claim -- the gate would accuse the system's own honest report."""
    from jarvis.execution.summary import USER_STATUS_MARKER

    ghost = tmp_path / "ghost_chart.png"          # declared, never written
    envelopes = [
        ExecutionEnvelope(
            execution_id="a", capability="plot_data", status="success",
            inputs_digest="d", artifacts=[str(ghost)], normalized_output=str(ghost),
        ).model_dump()
    ]
    answer = (
        "Grafiği hazırladım.\n\n"
        f"{USER_STATUS_MARKER}\n"
        f"- plot_data: reported successful by the tool, but independent "
        f"verification FAILED -- {ghost}"
    )
    log = _patch_llm(monkeypatch, HONEST)
    out = await _node("enforce_reversible")(_state(answer=answer, envelopes=envelopes))

    assert out == {}, "the system's own status block must not trip the gate"
    assert log == []
    assert _gate_rows(rollout_metrics_file)[0]["fired"] is False


async def test_a_fabrication_before_the_status_block_is_still_caught(
    monkeypatch, rollout_metrics_file, tmp_path
):
    """The split must not become a way to smuggle a claim past the gate."""
    from jarvis.execution.summary import USER_STATUS_MARKER

    answer = (
        "Grafiği oluşturdum ve C:/nope/uydurma_chart.png konumuna kaydettim.\n\n"
        f"{USER_STATUS_MARKER}\n- plot_data: FAILED -- did not complete"
    )
    _patch_llm(monkeypatch, HONEST)
    out = await _node("enforce_reversible")(_state(answer=answer))
    assert out["response"] == HONEST
    assert _gate_rows(rollout_metrics_file)[0]["fired"] is True


async def test_a_provider_error_during_repair_does_not_kill_the_turn(
    monkeypatch, rollout_metrics_file
):
    """The verification layer must never be the thing that breaks a turn --
    the same discipline audit_log and postcondition_runner already follow.
    And it must fail toward honesty: a failed retry is not a reason for the
    contradicted draft to survive."""
    class _Broken:
        async def ainvoke(self, messages, config=None):
            raise RuntimeError("provider exploded")

    monkeypatch.setattr(providers, "get_llm", lambda role, settings=None, **k: _Broken())
    out = await _node("enforce_reversible")(_state())
    assert "uydurma_chart.png" not in out["response"]
    assert _gate_rows(rollout_metrics_file)[0]["blocked"] is True


async def test_a_broken_repair_in_shadow_never_even_calls_the_provider(monkeypatch):
    class _Explodes:
        async def ainvoke(self, messages, config=None):
            raise AssertionError("shadow must not invoke a repair")

    monkeypatch.setattr(providers, "get_llm", lambda role, settings=None, **k: _Explodes())
    assert await _node("shadow")(_state()) == {}
