"""Patch 1.2 Faz 1D — turn compaction + turn-based history window.

Regression anchor A2→A3 (2026-07-16 manual round 2): on turn A2 ("Benim en
sevdiğim renk mavi, aklında tut") the local model hallucinated a ~20-call
tool batch; the gate blocked it, but ~22 junk messages (stub ToolMessages +
ack) entered history, and the old flat 20-MESSAGE trim evicted the user's
actual sentence. One turn later (A3, "en sevdiğim renk neydi?") the model
truthfully had no trace of it. With compaction, that turn persists as at
most 3 messages and the fact survives any following window math.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from jarvis.agent import (
    _compact_completed_turn_for_history,
    _execution_summary_from_ledger,
    _trim_history,
)


# ── _compact_completed_turn_for_history ───────────────────────────────────────

def test_compact_is_user_message_plus_final_answer_only():
    human = HumanMessage(content="Benim en sevdiğim renk mavi, aklında tut")
    exchange = _compact_completed_turn_for_history(human, "Not ettim: mavi.")

    assert len(exchange) == 2
    assert exchange[0] is human
    assert isinstance(exchange[1], AIMessage)
    assert exchange[1].content == "Not ettim: mavi."
    assert not any(isinstance(m, ToolMessage) for m in exchange)


def test_compact_with_summary_adds_single_line():
    human = HumanMessage(content="oku")
    summary = _execution_summary_from_ledger(
        [{"tool": "url_read", "fingerprint": "f", "ok": False, "content_head": "[ERROR] Refusing"}]
    )
    exchange = _compact_completed_turn_for_history(human, "Engellendi.", summary)

    assert len(exchange) == 3
    assert exchange[1].content == "[Tool execution summary: url_read failed/blocked]"


def test_summary_dedupes_identical_outcomes():
    ledger = [
        {"tool": "procedure_save", "ok": True},
        *[{"tool": "procedure_save", "ok": False} for _ in range(9)],
    ]
    s = _execution_summary_from_ledger(ledger)
    assert s.count("procedure_save") == 2  # one ok + one failed/blocked, not 10 lines
    assert _execution_summary_from_ledger([]) == ""
    assert _execution_summary_from_ledger(None) == ""


# ── _trim_history: turn-based window ──────────────────────────────────────────

def _turn(i: int) -> list:
    return [HumanMessage(content=f"soru {i}"), AIMessage(content=f"cevap {i}")]


def test_trim_keeps_last_n_turns():
    msgs = []
    for i in range(12):
        msgs += _turn(i)
    out = _trim_history(msgs, max_turns=10)

    humans = [m.content for m in out if isinstance(m, HumanMessage)]
    assert humans == [f"soru {i}" for i in range(2, 12)]
    assert len(out) == 20


def test_trim_never_splits_a_turn():
    msgs = _turn(0) + [HumanMessage(content="soru 1"),
                       AIMessage(content="[Tool execution summary: file_read ok]"),
                       AIMessage(content="cevap 1")]
    out = _trim_history(msgs, max_turns=1)
    # the whole 3-message turn survives, not a tail slice of it
    assert [m.content for m in out] == ["soru 1", "[Tool execution summary: file_read ok]", "cevap 1"]


def test_trim_preserves_system_messages():
    msgs = [SystemMessage(content="sys")] + _turn(0) + _turn(1)
    out = _trim_history(msgs, max_turns=1)
    assert isinstance(out[0], SystemMessage)
    assert [m.content for m in out[1:]] == ["soru 1", "cevap 1"]


def test_trim_under_limit_is_identity():
    msgs = _turn(0) + _turn(1)
    assert _trim_history(msgs, max_turns=10) == msgs


# ── the A2→A3 regression, end to end over the two helpers ────────────────────

def test_a2_hallucination_batch_cannot_evict_the_fact():
    """Reconstruct A2's damage with the OLD persistence shape, then show the
    NEW pipeline keeps the fact in the very next turn's context."""
    history: list = []

    # Turn A2 — model hallucinated ~20 calls; gate stubbed them all.
    a2_human = HumanMessage(content="Benim en sevdiğim renk mavi, aklında tut")
    a2_ledger = [
        {"tool": t, "ok": False}
        for t in ["itu_mail", "finance", "geo_math", "hud_panels", "procedure_save"] * 4
    ]
    exchange = _compact_completed_turn_for_history(
        a2_human, "Bu bilgiyi aklımda tutacağım.",
        _execution_summary_from_ledger(a2_ledger),
    )
    history = _trim_history(history + exchange, max_turns=10)

    # The polluted turn persists as ≤3 messages, fact included.
    assert len(history) <= 3
    assert any("mavi" in str(m.content) for m in history if isinstance(m, HumanMessage))

    # Turn A3 — the next turn's model context contains the fact.
    a3_context = [SystemMessage(content="sys")] + history + [
        HumanMessage(content="en sevdiğim renk neydi?")
    ]
    assert any(
        isinstance(m, HumanMessage) and "mavi" in str(m.content) for m in a3_context
    ), "A2'nin cümlesi bir sonraki turn'ün bağlamında kalmalı"
