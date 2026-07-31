"""jarvis/finance_parser.py -- the deterministic path, scored against ground truth.

Every case here comes from scripts/seed_finance_fixture.py's corpus, whose
``expect`` blocks are the oracle. Asserting against the fixture rather than
against hand-written duplicates means the parser and the gate can never disagree
about what "correct" is.

The governing rule under test is the owner's: **never guess a financial value.**
The extractor this replaces was explicitly instructed to guess -- its schema said
"use today's date if not found", its prompt said "make your best guess" -- so a
mail with an unreadable date silently became a transaction dated today. Several
tests below exist only to prove that particular behavior is gone.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from jarvis.finance_parser import (  # noqa: E402
    REJECT_NO_AMOUNT,
    REJECT_NO_DATE,
    REJECT_NOT_A_TRANSACTION,
    ParsedTransaction,
    ParseRejection,
    _to_float,
    looks_like_transaction,
    parse_transaction,
)
from seed_finance_fixture import build_fixture  # noqa: E402

FIXTURE = build_fixture(2026, 7)
MESSAGES = {}
for _m in FIXTURE["messages"]:
    # The duplicate-uid entry shares burgan-001's id; keep the first (they have
    # identical content, and dedup is FinanceStore's job, not the parser's).
    MESSAGES.setdefault(_m["id"], _m)


def _parse(msg: dict):
    return parse_transaction(msg["subject"], msg["body"], msg.get("date") or "")


def _transaction_cases():
    seen = set()
    for m in FIXTURE["messages"]:
        if m["expect"].get("outcome") != "transaction" or m["id"] in seen:
            continue
        seen.add(m["id"])
        yield pytest.param(m, id=m["id"])


def _rejection_cases():
    for m in FIXTURE["messages"]:
        if m["expect"].get("outcome") == "rejected":
            yield pytest.param(m, id=f"{m['id']}-{m['expect']['reason']}")


# ── Turkish number format ─────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("250,75", 250.75),
    ("1.850,00", 1850.00),
    ("42.500,00", 42500.00),
    ("1.234.567,89", 1234567.89),
    ("310", 310.0),
])
def test_turkish_grouping_is_read_correctly(raw, expected):
    """'1.850,00' is one thousand eight hundred fifty. Reading it as en-US gives
    1.85 -- three orders of magnitude lost on a number someone budgets against."""
    assert _to_float(raw) == expected


# ── every transaction in the corpus ───────────────────────────────────────────

@pytest.mark.parametrize("msg", list(_transaction_cases()))
def test_corpus_transactions_parse_exactly(msg):
    result = _parse(msg)
    expect = msg["expect"]

    assert isinstance(result, ParsedTransaction), f"rejected instead: {result}"
    assert result.amount == pytest.approx(expect["amount"]), "amount/sign"
    assert result.currency == expect["currency"]
    assert result.direction == expect["direction"]
    assert result.date == expect["date"], "date must come from the mail, not now()"


@pytest.mark.parametrize("msg", list(_transaction_cases()))
def test_corpus_merchants_are_recovered(msg):
    """Merchant drives categorisation, so a blank one degrades the whole
    category sheet even when the amount is right."""
    result = _parse(msg)
    assert isinstance(result, ParsedTransaction)
    expected_merchant = msg["expect"].get("merchant", "")
    if expected_merchant:
        assert expected_merchant.upper() in result.merchant.upper()


# ── every rejection in the corpus ─────────────────────────────────────────────

@pytest.mark.parametrize("msg", list(_rejection_cases()))
def test_corpus_rejections_are_rejected_with_the_right_reason(msg):
    result = _parse(msg)

    assert isinstance(result, ParseRejection), f"parsed instead: {result}"
    assert result.reason == msg["expect"]["reason"]
    assert result.detail, "a rejection must say why -- a silent skip is a bug report lost"


def test_marketing_mail_with_a_big_number_is_not_a_transaction():
    """'100.000 TL'ye varan kredi' is an advertisement. An LLM handed this will
    find the headline figure; the deterministic gate refuses it first."""
    result = parse_transaction(
        "Size Ozel Kredi Firsati!",
        "100.000 TL'ye varan ihtiyac kredisi firsatini kacirmayin. "
        "Kampanya kosullari icin web sitemizi ziyaret edin.",
        "Wed, 15 Jul 2026 08:00:00 +0300",
    )
    assert isinstance(result, ParseRejection)
    assert result.reason == REJECT_NOT_A_TRANSACTION


def test_no_date_anywhere_is_rejected_not_dated_today():
    """The single most important regression in this file."""
    result = parse_transaction(
        "Burgan Bank - Kartli Islem Bilgilendirmesi",
        "Kartiniz ile CARREFOUR isyerinde 675,25 TL tutarinda alisveris "
        "islemi gerceklestirilmistir.",
        "",  # no Date header either
    )
    assert isinstance(result, ParseRejection)
    assert result.reason == REJECT_NO_DATE


def test_date_header_is_used_when_the_body_has_no_date():
    """A real fallback, not a guess: the header is evidence of when the bank
    sent the notification."""
    result = parse_transaction(
        "Burgan Bank - Kartli Islem Bilgilendirmesi",
        "Kartiniz ile CARREFOUR isyerinde 675,25 TL tutarinda alisveris "
        "islemi gerceklestirilmistir.",
        "Thu, 16 Jul 2026 12:30:00 +0300",
    )
    assert isinstance(result, ParsedTransaction)
    assert result.date.startswith("2026-07-16")
    assert result.amount == pytest.approx(-675.25)


def test_transaction_wording_without_an_amount_is_rejected():
    result = parse_transaction(
        "Islem Bilgilendirmesi",
        "16.07.2026 tarihinde kartiniz ile bir alisveris islemi "
        "gerceklestirilmistir. Detaylar icin mobil uygulamamizi kullaniniz.",
        "Thu, 16 Jul 2026 12:00:00 +0300",
    )
    assert isinstance(result, ParseRejection)
    assert result.reason == REJECT_NO_AMOUNT


def test_an_impossible_date_is_not_accepted():
    """31.02 is date-shaped but not a date; it must not become a transaction via
    a silent fallback to something else in the string."""
    result = parse_transaction(
        "Burgan Bank - Islem",
        "31.02.2026 14:00 tarihinde kartiniz ile MIGROS isyerinde "
        "100,00 TL tutarinda alisveris islemi gerceklestirilmistir.",
        "",
    )
    assert isinstance(result, ParseRejection)
    assert result.reason == REJECT_NO_DATE


# ── direction / sign ──────────────────────────────────────────────────────────

def test_refund_is_income_not_another_expense():
    """A refund credits the account. Signing it negative double-counts the
    original purchase and makes the month look twice as expensive."""
    result = parse_transaction(
        "Iade Bilgilendirmesi",
        "13.07.2026 11:15 tarihinde TEKNOSA isyerinden yapilan 4.299,90 TL "
        "tutarindaki alisverisin iadesi hesabiniza yapilmistir.",
        "",
    )
    assert isinstance(result, ParsedTransaction)
    assert result.amount > 0
    assert result.direction == "income"


def test_outgoing_transfer_is_an_expense():
    result = parse_transaction(
        "EFT Islemi",
        "10.07.2026 16:20 tarihinde hesabinizdan AHMET YILMAZ adina "
        "3.000,00 TL tutarinda EFT islemi gerceklestirilmistir.",
        "",
    )
    assert isinstance(result, ParsedTransaction)
    assert result.amount == pytest.approx(-3000.00)
    assert result.category == "transfer"


def test_non_try_currency_is_preserved_not_coerced():
    """Silently relabelling USD as TRY would corrupt every downstream total."""
    result = parse_transaction(
        "Doviz Hesabi Islemi",
        "14.07.2026 10:00 tarihinde kartiniz ile AMAZON EU isyerinde "
        "120,50 USD tutarinda alisveris islemi gerceklestirilmistir.",
        "",
    )
    assert isinstance(result, ParsedTransaction)
    assert result.currency == "USD"
    assert result.amount == pytest.approx(-120.50)


def test_interest_rate_percentage_is_not_read_as_an_amount():
    """'%2,89' has no currency token next to it, which is exactly why the amount
    pattern requires one."""
    assert looks_like_transaction("Kredi", "yillik %2,89 faiz oranli") is False


# ── categorisation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("merchant,body,expected", [
    ("MIGROS TICARET AS", "MIGROS TICARET AS isyerinde", "food"),
    ("SHELL PETROL", "SHELL PETROL isyerinde", "transport"),
    ("TEKNOSA", "TEKNOSA isyerinde", "shopping"),
    ("ATM", "ATM'den nakit cekim", "atm"),
])
def test_expense_categories(merchant, body, expected):
    result = parse_transaction(
        "Islem", f"07.07.2026 14:00 tarihinde hesabinizdan {body} "
                 f"100,00 TL tutarinda odeme yapilmistir.", "",
    )
    assert isinstance(result, ParsedTransaction)
    assert result.category == expected


def test_salary_is_only_salary_when_money_comes_in():
    """A payment TO a school is not income just because 'maas' appears nearby."""
    incoming = parse_transaction(
        "Odeme", "10.07.2026 03:00 tarihinde hesabiniza 42.500,00 TL tutarinda "
                 "MAAS ODEMESI alacak kaydi yapilmistir.", "",
    )
    assert isinstance(incoming, ParsedTransaction)
    assert incoming.category == "salary"
    assert incoming.direction == "income"
