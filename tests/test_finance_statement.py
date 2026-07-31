"""jarvis/finance_statement.py — PDF bank statement parsing (Burgan / ON).

Built against a real 9-page export from the owner's account, because the format
facts that matter are not guessable:

* Amounts carry THREE decimal places — ``-140,000`` is −140.00 TL, not −140000.
  Confirmed against the bank's own running-balance column, the strongest oracle
  available: MIGROS ``-847,360`` moves 3.383,070 → 2.535,710, a difference of
  exactly 847.36. Getting this wrong scales every figure by 1000.
* pdfplumber reports page 1 as 4 columns and pages 2-8 as 6 (the same data with an
  empty leading and trailing cell). Filtering on ``len(cells) == 4`` found 10 rows
  out of 91.
* A row straddling a page break is emitted **twice** — once at the foot of a page
  carrying only the description prefix, once at the head of the next carrying the
  merchant. Keying identity on the description let both through and double-counted
  a transaction, throwing the month's expense total out by 151.44.

Every row literal below is copied from that real export. They drive ``parse_rows``
directly rather than a generated PDF: the risky logic is all in row handling, and
generating PDFs would mean adding a dependency for tests alone. The thin
pdfplumber extraction on top was validated by running the real file (91 rows read,
90 transactions, and the bank's own balance column consistent across 89/89
consecutive pairs).
"""
from __future__ import annotations

import pytest

from jarvis.finance_parser import ParsedTransaction, _to_float
from jarvis.finance_statement import (
    _row_uid,
    extract_merchant,
    parse_rows,
    parse_statement,
)

HEADER = ["Tarih", "Açıklama", "Tutar", "Bakiye"]


# ── the number format, which is the highest-stakes detail ─────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("-140,000", -140.00),      # three decimals, NOT -140000
    ("-799,990", -799.99),
    ("3.000,000", 3000.00),
    ("-5.000,000", -5000.00),
    ("-151,440", -151.44),
    ("-0,900", -0.90),
    ("2.118,900", 2118.90),
    ("10.000,000", 10000.00),
])
def test_statement_amounts_have_three_decimals(raw, expected):
    """Every one of these appears verbatim in the owner's real statement."""
    assert _to_float(raw) == pytest.approx(expected)


def test_balance_delta_confirms_the_scale():
    """The bank computed this, so it is an independent check on our parsing:
    3.383,070 − 847,360 = 2.535,710."""
    assert _to_float("3.383,070") + _to_float("-847,360") == pytest.approx(
        _to_float("2.535,710"), abs=0.01
    )


# ── identity / dedup ──────────────────────────────────────────────────────────

def test_uid_ignores_the_description():
    """The page-straddling duplicate carries the SAME date/amount/balance and a
    DIFFERENT description. Including the description in the key gave the two
    halves different ids and the transaction was counted twice."""
    assert _row_uid("2026-07-18T00:00:00", -151.44, 1715.00) == _row_uid(
        "2026-07-18T00:00:00", -151.44, 1715.00
    )


def test_uid_separates_identical_purchases_on_the_same_day():
    """Two ESPRESSOLAB −140,00 charges on 30.07 are distinct transactions; the
    running balance is what tells them apart."""
    assert _row_uid("2026-07-30T00:00:00", -140.00, 2118.90) != _row_uid(
        "2026-07-30T00:00:00", -140.00, 2258.90
    )


# ── merchant extraction, from real description shapes ─────────────────────────

@pytest.mark.parametrize("description,expected", [
    ("POS- Satış-535806*****5560- p.No-181623- 621162036818- ESPRESSOLAB ISTANBUL TR",
     "ESPRESSOLAB ISTANBUL TR"),
    ("POS- Satış-535806*****5560- p.No-186128- 620918318699-TABACCO Istanbul TR",
     "TABACCO Istanbul TR"),
    ("Gönderen Adı:ÖZGE ALTÜRK SN:6977473105 GönBanka:10 FastRef:1071008 Fast Muhasebe",
     "ÖZGE ALTÜRK"),
    ("Gönderen Adı:İLKNUR AKGÜNLÜ SN:1529691539 Dügün GönBanka:12 FastRef:1022884",
     "İLKNUR AKGÜNLÜ"),
])
def test_merchant_is_recovered_from_real_description_shapes(description, expected):
    assert extract_merchant(description) == expected


def test_bank_fees_are_labelled_not_left_as_noise():
    assert extract_merchant("KGV Debit Kart Yurtdışı Kullanım") == "BANKA MASRAFI"


# ── row handling ──────────────────────────────────────────────────────────────

def test_parses_rows_end_to_end():
    r = parse_rows([
        HEADER,
        ["30.07.2026", "POS-\nSatış-535806*****5560-\np.No-181623-\n621162036818-\n"
                       "ESPRESSOLAB\nISTANBUL TR", "-140,000", "2.118,900"],
        ["30.07.2026", "Gönderen Adı:İLKNUR\nAKGÜNLÜ SN:1529691539\nGönBanka:12",
         "3.000,000", "200,490"],
        ["30.07.2026", "KGV Debit Kart Yurtdışı\nKullanım", "-1,600", "2.260,500"],
    ])

    assert len(r.transactions) == 3
    assert not r.rejections
    assert sorted(t.amount for t in r.transactions) == pytest.approx(
        [-140.00, -1.60, 3000.00]
    )

    credit = next(t for t in r.transactions if t.amount > 0)
    assert credit.direction == "income"
    assert credit.merchant == "İLKNUR AKGÜNLÜ"

    debit = next(t for t in r.transactions if t.amount == pytest.approx(-140.0))
    assert debit.direction == "expense"
    assert "ESPRESSOLAB" in debit.merchant
    assert debit.currency == "TRY"
    assert debit.date.startswith("2026-07-30")


def test_six_column_rows_are_not_skipped():
    """Pages 2-8 of the real export come back as 6 columns — the same data with an
    empty leading and trailing cell. Requiring exactly 4 found 10 rows out of 91."""
    r = parse_rows([
        ["", "28.07.2026", "POS-|Satış|p.No-186128-|620918318699-TABACCO|Istanbul TR",
         "-20,000", "120,490", ""],
        ["", "27.07.2026", "POS-|Satış|p.No-142654-|620821884789-PAYCEL|/GETIR1 34 TR",
         "-536,970", "657,460", ""],
    ])

    assert len(r.transactions) == 2


def test_a_row_repeated_across_a_page_break_is_counted_once():
    """Verbatim from the real export: 18.07.2026 / -151,440 / 1.715,000 appears at
    the foot of page 4 with only the POS prefix, and again at the head of page 5
    with the merchant. Both halves are real rows; only one is a real transaction."""
    r = parse_rows([
        ["", "18.07.2026", "POS-\nSatış-535806*****5560-\np.No-029124-", "", "", ""],
        ["", "18.07.2026", "", "-151,440", "1.715,000", ""],
        ["", "", "p.No-029124-", "", "", ""],
        ["", "18.07.2026", "619915430050-\nIYZICO/UBER.COM\nISTANBUL TR",
         "-151,440", "1.715,000", ""],
        ["", "16.07.2026", "POS-\nSatış\np.No-049838-\n619743803231-MOKA\nUNITED",
         "-307,500", "2.022,500", ""],
    ])

    assert r.duplicates == 1
    assert len(r.transactions) == 2, "the split row must collapse to one"
    assert sum(t.amount for t in r.transactions) == pytest.approx(-151.44 + -307.50)
    kept = next(t for t in r.transactions if t.amount == pytest.approx(-151.44))
    assert "UBER" in kept.merchant.upper(), "keep the half that carries the merchant"


def test_orphan_fragment_is_attached_to_the_next_dated_row():
    r = parse_rows([
        ["", "", "p.No-029124-", "", "", ""],
        ["", "18.07.2026", "619915430050-\nIYZICO/UBER.COM\nISTANBUL TR",
         "-151,440", "1.715,000", ""],
    ])

    assert len(r.transactions) == 1
    assert "p.No-029124-" in r.transactions[0].description


def test_header_row_is_not_a_transaction():
    r = parse_rows([HEADER])
    assert not r.transactions
    assert not r.rejections


def test_zero_amount_row_is_rejected_with_a_reason():
    r = parse_rows([["30.07.2026", "Bir şey", "0,000", "100,000"]])
    assert not r.transactions
    assert sum(r.rejections.values()) == 1


def test_row_without_an_amount_is_rejected_not_guessed():
    r = parse_rows([["30.07.2026", "Açıklama ama tutar yok", "", ""]])
    assert not r.transactions
    assert sum(r.rejections.values()) == 1


def test_sign_comes_from_the_cell_never_inferred():
    """A statement states the direction; unlike a notification mail it never has
    to be guessed from wording."""
    r = parse_rows([
        ["30.07.2026", "İLKER ÖZTÜRK Diğer havale bedeli", "10.000,000", "100,000"],
        ["30.07.2026", "POS- Satış p.No-1- 62116-MIGROS ISTANBUL TR",
         "-847,360", "3.383,070"],
    ])

    by_amount = {t.amount: t for t in r.transactions}
    assert by_amount[10000.00].direction == "income"
    assert by_amount[-847.36].direction == "expense"


def test_every_parsed_row_is_a_ParsedTransaction():
    """Downstream (store, workbook, chart) is typed against this — a statement row
    must be indistinguishable from a mail-derived one."""
    r = parse_rows([["30.07.2026", "POS- p.No-1- 62116-ESPRESSOLAB ISTANBUL TR",
                     "-140,000", "2.118,900"]])
    assert all(isinstance(t, ParsedTransaction) for t in r.transactions)


# ── file-level guards ─────────────────────────────────────────────────────────

def test_missing_file_is_a_message_not_an_exception(tmp_path):
    r = parse_statement(tmp_path / "yok.pdf")
    assert r.error.startswith("[ERROR]")
    assert not r.transactions


def test_non_pdf_is_refused_clearly(tmp_path):
    csv = tmp_path / "ekstre.csv"
    csv.write_text("a,b", encoding="utf-8")
    r = parse_statement(csv)
    assert r.error.startswith("[ERROR]")
    assert "PDF" in r.error


def test_a_corrupt_pdf_is_a_message_not_a_crash(tmp_path):
    bad = tmp_path / "bozuk.pdf"
    bad.write_bytes(b"not really a pdf")
    r = parse_statement(bad)
    assert r.error.startswith("[ERROR]")


# ── path confinement ──────────────────────────────────────────────────────────

def test_import_statement_is_confined_like_file_read(jarvis_home, isolated_cwd):
    """import_statement must not become a way around file_read's boundary.

    Reading an arbitrary file is a capability, not a detail, even when the tool
    only looks for transaction rows in it. The first version used
    Path(...).expanduser(), which both bypassed JARVIS_HOME isolation (caught by
    tests/test_no_host_path_leak) and let any absolute path through.
    """
    from types import SimpleNamespace

    from jarvis.tools.finance import finance_control

    outside = isolated_cwd.parent / "gizli.pdf"
    outside.write_bytes(b"%PDF-1.4 not really")

    result = finance_control(
        action="import_statement", path=str(outside),
        workspace=jarvis_home, settings=SimpleNamespace(),
    )

    assert result.startswith("[ERROR]")
    assert "outside the workspace" in result


def test_import_statement_requires_a_path():
    from types import SimpleNamespace

    from jarvis.tools.finance import finance_control

    result = finance_control(action="import_statement", settings=SimpleNamespace())
    assert "path gerekli" in result


# ── categorisation of the owner's real merchants ──────────────────────────────

@pytest.mark.parametrize("merchant,expected", [
    ("ESPRESSOLAB ISTANBUL TR", "food"),
    ("SOFRA BOREK ISTANBUL TR", "food"),
    ("MOKA UNITED/SWALLET ISTANBUL TR", "food"),
    ("MIGROS - MJET ARNAVUTKOY ISTANBUL TR", "food"),
    ("Spotify Stockholm SWE", "entertainment"),
    ("Disney Plus London GBR", "entertainment"),
    ("IYZICO/UBER.COM ISTANBUL TR", "transport"),
    ("Google Claude by Anth London GBR", "bills"),
    ("ITU STRATEJI GELISTIRME DISTANBUL TR", "education"),
])
def test_real_merchants_get_a_useful_category(merchant, expected):
    """Before these rules 71 of 90 real transactions landed in "other", which makes
    the workbook's Kategori sheet worthless."""
    r = parse_rows([["30.07.2026", f"POS- p.No-1- 62116-{merchant}",
                     "-100,000", "1.000,000"]])
    assert r.transactions[0].category == expected
