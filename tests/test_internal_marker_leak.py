"""The model must not hand the user JARVIS's own internal bookkeeping marker.

_execution_summary_from_ledger() writes "[Tool execution summary: finance ok]"
into conversation history so the next turn knows what actually ran. On
2026-07-30, a live follow-up turn ("grafikteki tarihler okunmuyor, düzelt") made
**zero tool calls** and opened its reply with a hand-written
"[Tool execution summary: plot_data ok]" — imitating the format it had seen in
history — then described a chart file (`cashflow_2026-07_line.png`) that was
never created.

Stripping the marker does not stop the fabrication; it stops a fabrication from
wearing a system-generated badge, which is what makes it read as authoritative.
The deeper fix (verifying file paths a response claims) is tracked in HANDOFF.md.
"""
from __future__ import annotations

import pytest

from jarvis.agent import _execution_summary_from_ledger, strip_internal_markers


def test_strips_the_marker_the_model_imitated():
    reply = ("[Tool execution summary: plot_data ok]\n\n"
             "✅ **Çizgi grafiği tamamlandı:** `exports/cashflow_2026-07_line.png`")

    cleaned = strip_internal_markers(reply)

    assert not cleaned.startswith("[Tool execution summary")
    assert "Çizgi grafiği tamamlandı" in cleaned, "the real answer must survive"


@pytest.mark.parametrize("marker", [
    "[Tool execution summary: finance ok]",
    "[Tool execution summary: finance ok; plot_data failed/blocked]",
    "[tool execution summary: x ok]",
])
def test_strips_every_shape_the_generator_can_produce(marker):
    assert strip_internal_markers(marker + " cevap") .strip() == "cevap"


def test_matches_what_the_generator_actually_emits():
    """Pins the two together: if the marker format changes, this fails rather
    than the filter silently going blind."""
    produced = _execution_summary_from_ledger([{"tool": "finance", "ok": True}])

    assert strip_internal_markers(produced + " metin").strip() == "metin"


def test_leaves_ordinary_answers_alone():
    for reply in (
        "Excel tablosu hazır.",
        "",
        "Bir [ERROR] oluştu.",
        "Sonuç: [Tool execution summary] diye bir şey yok burada, ortada geçiyor.",
    ):
        assert strip_internal_markers(reply) == reply


def test_only_the_leading_marker_is_removed():
    """A marker quoted mid-answer is content, not a badge — e.g. the user asking
    what that line means."""
    reply = "Şu satır ne demek: [Tool execution summary: finance ok]"

    assert strip_internal_markers(reply) == reply
