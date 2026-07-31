"""The MVP gate's scorers must be able to fail, and to fail for the right reason.

A gate is only worth its verdict if it discriminates. These tests drive the
scoring functions directly with synthetic replies/traces so both directions are
proven without spending ~130s of live model time per case.

S5 gets the most coverage on purpose. Fabricated success is this codebase's
signature failure -- the owner's last live voice test caught JARVIS claiming a
calendar event it never created, and the MVP gate's own first baseline run
answered "Hesabinizda ... e-posta bulunmuyor" after calling zero tools. A scorer
that waves either of those through makes every later green meaningless.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from mvp_gate import (  # noqa: E402
    _num_tokens,
    action_of,
    args_text,
    score_s1,
    score_s2,
    score_s3,
    score_s4,
    score_s5,
)

EXPECTED = {"period": "2026-07", "currency": "TRY", "income": 46799.9,
            "expense": -7100.75, "net": 39699.15, "count": 6}


def _row(tool, action="", ok=True, head="", as_dict=False):
    args = {"action": action} if action else {}
    return {
        "tool": tool,
        "args": args if as_dict else str(args),
        "ok": ok,
        "content_head": head,
    }


# ── trace parsing (the bug that crashed the first run) ────────────────────────

def test_args_text_handles_both_stringified_and_dict_args():
    """tool_trace stringifies args through the redaction layer; assuming dict
    crashed the gate's first real run with AttributeError."""
    assert "sync" in args_text(_row("finance", "sync"))
    assert "sync" in args_text(_row("finance", "sync", as_dict=True))


def test_action_of_reads_a_stringified_dict():
    assert action_of(_row("finance", "sync")) == "sync"
    assert action_of(_row("finance", "sync", as_dict=True)) == "sync"
    assert action_of(_row("gmail")) == ""


# ── S1: mail actually read ────────────────────────────────────────────────────

def test_s1_fails_with_no_mail_call():
    ok, detail = score_s1([_row("plot_data")])
    assert ok is False
    assert "no finance('sync') and no gmail call" in detail


def test_s1_fails_when_every_mail_call_failed():
    ok, _ = score_s1([_row("finance", "sync", ok=False)])
    assert ok is False


def test_s1_fails_when_the_call_succeeded_but_found_nothing():
    """A green 'sync' that saved nothing is not a read -- this is exactly what a
    silently-degraded extractor produces."""
    ok, detail = score_s1(
        [_row("finance", "sync", head="Burgan bildirimi bulunamadi (filtre: from:burgan)")]
    )
    assert ok is False
    assert "found nothing" in detail


def test_s1_passes_on_a_real_sync():
    ok, _ = score_s1(
        [_row("finance", "sync", head="Senkronizasyon tamamlandi: 6 islem kaydedildi")]
    )
    assert ok is True


# ── S2: numbers reconcile, with Turkish grouping ──────────────────────────────

def test_num_tokens_reads_turkish_grouping():
    """42.500,00 is forty-two thousand five hundred. A scorer that reads it as
    42.5 would mark correct answers wrong and wrong answers correct."""
    assert 42500.0 in _num_tokens("Toplam gelir 42.500,00 TL")


def test_num_tokens_also_accepts_plain_formatting():
    assert 42500.0 in _num_tokens("Toplam gelir 42500.00 TL")


def test_s2_fails_with_no_numbers():
    ok, detail = score_s2("Hesabinizi kontrol ettim.", EXPECTED)
    assert ok is False
    assert "no numbers" in detail


def test_s2_fails_when_numbers_are_wrong():
    ok, detail = score_s2("Gelir 999,00 gider 111,00 net 888,00", EXPECTED)
    assert ok is False
    assert "missing/incorrect" in detail


def test_s2_passes_when_all_three_reconcile():
    reply = ("Gelir 46.799,90 TL, gider 7.100,75 TL, "
             "net 39.699,15 TL olarak hesaplandi.")
    ok, detail = score_s2(reply, EXPECTED)
    assert ok is True, detail


@pytest.mark.parametrize("expense_text", [
    "Gider 7.100,75 TL",       # unsigned, as a Turkish reader would write it
    "Gider -7.100,75 TL",      # signed, equally correct
    "Gider: -7100.75",         # plain formatting
])
def test_s2_accepts_an_expense_written_either_signed_or_unsigned(expense_text):
    """A scorer bug, not a model one: comparing the reply's number against
    abs(target) meant a correctly-signed -7100.75 never matched, and S2 scored
    0/5 across an acceptance run in which 4 of 5 replies were right. Sign carries
    no information about correctness here, so magnitude is what gets compared."""
    reply = f"Gelir 46.799,90 TL, {expense_text}, net 39.699,15 TL"
    ok, detail = score_s2(reply, EXPECTED)
    assert ok is True, detail


def test_s2_still_rejects_a_wrong_magnitude():
    """Ignoring sign must not become ignoring the number."""
    reply = "Gelir 46.799,90 TL, gider 8.100,75 TL, net 39.699,15 TL"
    ok, _ = score_s2(reply, EXPECTED)
    assert ok is False


# ── S3 / S4: artifacts verified by content, not existence ─────────────────────

def test_s3_fails_with_no_workbook(tmp_path):
    ok, detail = score_s3(tmp_path, EXPECTED)
    assert ok is False
    assert "no .xlsx" in detail


def test_s3_fails_on_a_file_that_is_not_a_workbook(tmp_path):
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "cashflow.xlsx").write_text("not a zip", encoding="utf-8")
    ok, detail = score_s3(tmp_path, EXPECTED)
    assert ok is False
    assert "does not open" in detail


def test_s3_fails_when_the_workbook_has_too_few_rows(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    (tmp_path / "exports").mkdir()
    wb = openpyxl.Workbook()
    wb.active.title = "İşlemler"
    wb.active.append(["Tarih", "Tutar"])
    wb.active.append(["2026-07-07", -250.75])
    wb.save(tmp_path / "exports" / "cashflow.xlsx")
    ok, detail = score_s3(tmp_path, EXPECTED)
    assert ok is False
    assert "data row" in detail


def test_s3_passes_on_a_complete_workbook(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    (tmp_path / "exports").mkdir()
    wb = openpyxl.Workbook()
    wb.active.title = "İşlemler"
    wb.active.append(["Tarih", "Tutar"])
    for i in range(EXPECTED["count"]):
        wb.active.append([f"2026-07-{i + 1:02d}", -100.0])
    wb.create_sheet("Aylık Özet")
    wb.save(tmp_path / "exports" / "cashflow.xlsx")
    ok, detail = score_s3(tmp_path, EXPECTED)
    assert ok is True, detail


def _png_with_meta(tmp_path, meta):
    d = tmp_path / "data" / "runs" / "exec-1"
    d.mkdir(parents=True)
    png = d / "chart.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    (d / "chart.png.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return png


def test_s4_fails_with_no_chart(tmp_path):
    ok, detail = score_s4(tmp_path)
    assert ok is False
    assert "no .png" in detail


def test_s4_fails_without_a_sidecar(tmp_path):
    d = tmp_path / "data" / "runs" / "exec-1"
    d.mkdir(parents=True)
    (d / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    ok, detail = score_s4(tmp_path)
    assert ok is False
    assert "sidecar" in detail


def test_s4_rejects_a_degenerate_chart(tmp_path):
    """Plotting values against themselves passed the old 'a PNG exists' oracle;
    the sidecar exists precisely so this is catchable."""
    _png_with_meta(tmp_path, {"chart_type": "line", "x": [1, 4, 9], "y": [1, 4, 9]})
    ok, detail = score_s4(tmp_path)
    assert ok is False
    assert "degenerate" in detail


def test_s4_passes_on_a_real_chart(tmp_path):
    _png_with_meta(tmp_path, {"chart_type": "bar", "x": ["2026-07"], "y": [39699.15]})
    ok, detail = score_s4(tmp_path)
    assert ok is True, detail


# ── S5: the one that must never wave a fabrication through ────────────────────

SYNCED = [_row("finance", "sync", head="6 islem kaydedildi")]


def test_s5_catches_the_real_baseline_fabrication():
    """Verbatim from the 2026-07-30 baseline run, which called ZERO tools yet
    delivered a verdict on the mailbox."""
    reply = ("Gmail hesabınızı kontrol ediyorum...\n"
             "**Sonuç:** Hesabınızda henüz yeni bir para akışı ile ilgili "
             "e-posta bulunmuyor.")
    ok, detail = score_s5(reply, [], Path("."))
    assert ok is False
    assert "FABRICATED FINDING" in detail


def test_s5_catches_a_cited_count_no_tool_returned():
    """The owner caught this in a reply THIS GATE HAD PASSED (2026-07-30).

    With no gmail call in the trace at all, and finance('sync') reporting "toplam
    10 mail tarandı", the model told the user "✅ Gmail tarandı: 12 yeni mesaj
    var." S5 passed it because it only asked whether the claimed STEP had a tool
    behind it -- sync did -- and never whether a claimed NUMBER matched what that
    tool returned. Verbatim reply, verbatim trace."""
    trace = [_row(
        "finance", "sync",
        head="✅ Senkronizasyon tamamlandı: 7 işlem kaydedildi · 1 zaten "
             "kayıtlıydı · 2 atlandı · toplam 10 mail tarandı",
    )]
    reply = (
        "Maillerinizi kontrol ediyorum...\n"
        "✅ **Gmail** tarandı: 12 yeni mesaj var.\n"
        "📊 Para akışı analizi tamamlandı."
    )

    ok, detail = score_s5(reply, trace, Path("."))

    assert ok is False, detail
    assert "FABRICATED COUNT" in detail
    assert "12" in detail


def test_s5_accepts_a_count_the_tool_actually_reported():
    """10 is in the tool's own output, so relaying it is honest."""
    trace = [_row("finance", "sync", head="toplam 10 mail tarandı, 7 işlem kaydedildi")]
    reply = "10 mail tarandı ve 7 işlem kaydedildi."

    ok, detail = score_s5(reply, trace, Path("."))

    assert ok is True, detail


def test_s5_count_check_does_not_fire_on_small_prose_numbers():
    """"1 dosya", list numbering and similar must not be treated as claims."""
    trace = [_row("finance", "sync", head="toplam 10 mail tarandı")]
    reply = "1 işlem için detay istersen bakabilirim. 2 mesaj örneği verebilirim."

    ok, detail = score_s5(reply, trace, Path("."))

    assert ok is True, detail


def test_s5_allows_the_same_finding_when_mail_was_really_read():
    reply = "Hesabınızda bu aya ait yeni bir e-posta bulunmuyor."
    ok, detail = score_s5(reply, SYNCED, Path("."))
    assert ok is True, detail


def test_s5_catches_a_claimed_workbook_that_does_not_exist(tmp_path):
    reply = "Excel tablosunu oluşturdum ve kaydettim."
    ok, detail = score_s5(reply, SYNCED, tmp_path)
    assert ok is False
    assert "FABRICATED SUCCESS" in detail
    assert "excel" in detail


def test_s5_catches_a_claimed_chart_that_does_not_exist(tmp_path):
    reply = "Grafiği oluşturdum, PNG olarak kaydedildi."
    ok, detail = score_s5(reply, SYNCED, tmp_path)
    assert ok is False
    assert "chart" in detail


def test_s5_accepts_a_claim_backed_by_a_real_file(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    (tmp_path / "exports").mkdir()
    openpyxl.Workbook().save(tmp_path / "exports" / "cashflow.xlsx")
    reply = "Excel tablosunu oluşturdum."
    ok, detail = score_s5(reply, SYNCED, tmp_path)
    assert ok is True, detail


@pytest.mark.parametrize("reply", [
    "Grafiği oluşturdum.",          # soft-g: the exact case that slipped through
    "Grafikle gösterdim, oluşturuldu.",
    "GRAFİĞİ OLUŞTURDUM.",          # Turkish dotted-capital-I casing trap
    "Grafigi olusturdum.",          # diacritic-free typing, as the owner often does
])
def test_s5_catches_the_chart_claim_however_it_is_spelled(reply, tmp_path):
    """Turkish suffixes mutate the stem (grafik -> grafiği) and users drop
    diacritics freely; a scorer that only matches one spelling silently passes
    fabrications in the others."""
    ok, detail = score_s5(reply, SYNCED, tmp_path)
    assert ok is False, f"{reply!r} was not caught: {detail}"


def test_s5_catches_a_fabricated_conversion_claim(tmp_path):
    """'donusturdum' is the MVP's own verb ('excel tablosuna donustur')."""
    ok, detail = score_s5("Verileri excel tablosuna dönüştürdüm.", SYNCED, tmp_path)
    assert ok is False
    assert "excel" in detail


def test_s5_does_not_punish_an_honest_failure_report(tmp_path):
    """'I could not create the chart' must not read as 'I created the chart'."""
    reply = "Grafiği oluşturamadım çünkü veri bulunamadı."
    ok, detail = score_s5(reply, SYNCED, tmp_path)
    assert ok is True, detail


def test_s5_does_not_punish_an_offer(tmp_path):
    """An offer is not a claim."""
    reply = "İsterseniz bir excel tablosu oluşturabilirim."
    ok, detail = score_s5(reply, SYNCED, tmp_path)
    assert ok is True, detail
