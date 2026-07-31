"""Burgan-shaped mail fixture corpus with known ground truth (MVP lane A).

Why this exists: the MVP chain starts at "read my mail", but --profile test runs
against a temp JARVIS_HOME with no Gmail token, and the real mailbox is neither
repeatable nor safe to depend on for a 5-run measurement. This module is the
single source of truth for a deterministic corpus that:

  * exercises every transaction shape the parser must handle, AND
  * deliberately includes the shapes it must REFUSE to guess at.

The ``expect`` block on each message is the oracle -- tests/mvp_gate assert
against it rather than against whatever the parser happens to produce, so a
parser regression cannot quietly redefine "correct".

Deliberately adversarial entries (owner review, 2026-07-30):
  * a non-transaction Burgan mail (marketing) -> must be rejected, not parsed
  * a duplicate email uid                     -> must dedup to ONE transaction
  * a mail with no date anywhere              -> must be rejected, never now()
  * a refund/income credit                    -> sign must flip to positive
  * an outgoing transfer                      -> expense, distinct from a card spend
  * a non-TRY amount                          -> must never enter a TRY aggregate
  * a transaction-shaped mail with no amount  -> must be rejected

Usage:
    python scripts/seed_finance_fixture.py --mails data/test_fixtures/burgan_mails.json
    python scripts/seed_finance_fixture.py --db <path/to/sessions.db>
    python scripts/seed_finance_fixture.py --month 2026-07 --mails out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SENDER = "bilgilendirme@burganbank.com.tr"
SCHEMA_VERSION = 1


def _corpus(year: int, month: int) -> list[dict]:
    """The corpus, anchored to one month so period filtering is testable.

    Amounts use the real Turkish convention (1.234,56) on purpose -- a parser
    that assumes en-US grouping reads 1.234,56 as 1.234 and silently loses
    three orders of magnitude. That failure must be caught here, not in
    production.
    """
    ym = f"{year:04d}-{month:02d}"
    # A previous-month entry proves the period filter actually filters.
    pm_year, pm_month = (year - 1, 12) if month == 1 else (year, month - 1)
    pym = f"{pm_year:04d}-{pm_month:02d}"

    return [
        {
            "id": "burgan-001",
            "subject": "Burgan Bank - Kartli Islem Bilgilendirmesi",
            "date": f"Tue, 07 {_mon(month)} {year} 14:32:11 +0300",
            "body": (
                "Sayin musterimiz,\n"
                f"07.{month:02d}.{year} 14:32 tarihinde 5312****4417 numarali kartiniz ile "
                "MIGROS TICARET AS isyerinde 250,75 TL tutarinda alisveris islemi "
                "gerceklestirilmistir.\n"
                "Burgan Bank"
            ),
            "expect": {
                "outcome": "transaction",
                "date": f"{ym}-07T14:32:00",
                "amount": -250.75, "currency": "TRY",
                "direction": "expense", "merchant": "MIGROS TICARET AS",
            },
        },
        {
            "id": "burgan-002",
            "subject": "Burgan Bank - Kartli Islem Bilgilendirmesi",
            "date": f"Wed, 08 {_mon(month)} {year} 09:05:40 +0300",
            "body": (
                f"08.{month:02d}.{year} 09:05 tarihinde 5312****4417 numarali kartiniz ile "
                "SHELL PETROL isyerinde 1.850,00 TL tutarinda alisveris islemi "
                "gerceklestirilmistir."
            ),
            "expect": {
                "outcome": "transaction",
                "date": f"{ym}-08T09:05:00",
                "amount": -1850.00, "currency": "TRY",
                "direction": "expense", "merchant": "SHELL PETROL",
            },
        },
        {
            "id": "burgan-003",
            "subject": "Burgan Bank - Hesabiniza Gelen Odeme",
            "date": f"Fri, 10 {_mon(month)} {year} 03:00:00 +0300",
            "body": (
                f"10.{month:02d}.{year} 03:00 tarihinde hesabiniza 42.500,00 TL tutarinda "
                "MAAS ODEMESI alacak kaydi yapilmistir."
            ),
            "expect": {
                "outcome": "transaction",
                "date": f"{ym}-10T03:00:00",
                "amount": 42500.00, "currency": "TRY",
                "direction": "income", "merchant": "MAAS ODEMESI",
            },
        },
        {
            "id": "burgan-004",
            "subject": "Burgan Bank - EFT Islemi",
            "date": f"Fri, 10 {_mon(month)} {year} 16:20:05 +0300",
            "body": (
                f"10.{month:02d}.{year} 16:20 tarihinde hesabinizdan AHMET YILMAZ adina "
                "3.000,00 TL tutarinda EFT islemi gerceklestirilmistir."
            ),
            "expect": {
                "outcome": "transaction",
                "date": f"{ym}-10T16:20:00",
                "amount": -3000.00, "currency": "TRY",
                "direction": "expense", "merchant": "AHMET YILMAZ",
            },
        },
        {
            "id": "burgan-005",
            "subject": "Burgan Bank - Nakit Cekim",
            "date": f"Sat, 11 {_mon(month)} {year} 19:44:00 +0300",
            "body": (
                f"11.{month:02d}.{year} 19:44 tarihinde ATM'den 2.000,00 TL tutarinda "
                "nakit cekim islemi gerceklestirilmistir."
            ),
            "expect": {
                "outcome": "transaction",
                "date": f"{ym}-11T19:44:00",
                "amount": -2000.00, "currency": "TRY",
                "direction": "expense", "merchant": "ATM",
            },
        },
        {
            "id": "burgan-006",
            "subject": "Burgan Bank - Iade Bilgilendirmesi",
            "date": f"Mon, 13 {_mon(month)} {year} 11:15:00 +0300",
            "body": (
                f"13.{month:02d}.{year} 11:15 tarihinde TEKNOSA isyerinden yapilan "
                "4.299,90 TL tutarindaki alisverisin iadesi hesabiniza yapilmistir."
            ),
            "expect": {
                "outcome": "transaction",
                "date": f"{ym}-13T11:15:00",
                "amount": 4299.90, "currency": "TRY",
                "direction": "income", "merchant": "TEKNOSA",
            },
        },
        {
            # Must never be summed into a TRY total.
            "id": "burgan-007",
            "subject": "Burgan Bank - Doviz Hesabi Islemi",
            "date": f"Tue, 14 {_mon(month)} {year} 10:00:00 +0300",
            "body": (
                f"14.{month:02d}.{year} 10:00 tarihinde kartiniz ile AMAZON EU isyerinde "
                "120,50 USD tutarinda alisveris islemi gerceklestirilmistir."
            ),
            "expect": {
                "outcome": "transaction",
                "date": f"{ym}-14T10:00:00",
                "amount": -120.50, "currency": "USD",
                "direction": "expense", "merchant": "AMAZON EU",
            },
        },
        {
            # Same uid as burgan-001 -> exactly one stored transaction.
            "id": "burgan-001",
            "subject": "Burgan Bank - Kartli Islem Bilgilendirmesi (tekrar)",
            "date": f"Tue, 07 {_mon(month)} {year} 14:32:11 +0300",
            "body": (
                f"07.{month:02d}.{year} 14:32 tarihinde 5312****4417 numarali kartiniz ile "
                "MIGROS TICARET AS isyerinde 250,75 TL tutarinda alisveris islemi "
                "gerceklestirilmistir."
            ),
            "expect": {"outcome": "duplicate", "of": "burgan-001"},
        },
        {
            "id": "burgan-008",
            "subject": "Burgan Bank - Size Ozel Kredi Firsati!",
            "date": f"Wed, 15 {_mon(month)} {year} 08:00:00 +0300",
            "body": (
                "Degerli musterimiz, size ozel yillik %2,89 faiz oranli ihtiyac kredisi "
                "firsatini kacirmayin. 100.000 TL'ye varan kredi icin subelerimize bekleriz. "
                "Kampanya kosullari icin web sitemizi ziyaret edin."
            ),
            "expect": {"outcome": "rejected", "reason": "not_a_transaction"},
        },
        {
            # Transaction-shaped wording, no amount anywhere -> must not guess.
            "id": "burgan-009",
            "subject": "Burgan Bank - Islem Bilgilendirmesi",
            "date": f"Thu, 16 {_mon(month)} {year} 12:00:00 +0300",
            "body": (
                f"16.{month:02d}.{year} tarihinde kartiniz ile bir alisveris islemi "
                "gerceklestirilmistir. Detaylar icin mobil uygulamamizi kullaniniz."
            ),
            "expect": {"outcome": "rejected", "reason": "no_amount"},
        },
        {
            # No Date header AND no date in the body -> reject, never now().
            "id": "burgan-010",
            "subject": "Burgan Bank - Kartli Islem Bilgilendirmesi",
            "date": None,
            "body": (
                "Kartiniz ile CARREFOUR isyerinde 675,25 TL tutarinda alisveris islemi "
                "gerceklestirilmistir."
            ),
            "expect": {"outcome": "rejected", "reason": "no_date"},
        },
        {
            # Previous month -> parses fine, but must fall outside the period.
            "id": "burgan-011",
            "subject": "Burgan Bank - Kartli Islem Bilgilendirmesi",
            "date": f"Mon, 22 {_mon(pm_month)} {pm_year} 13:00:00 +0300",
            "body": (
                f"22.{pm_month:02d}.{pm_year} 13:00 tarihinde kartiniz ile GETIR isyerinde "
                "310,00 TL tutarinda alisveris islemi gerceklestirilmistir."
            ),
            "expect": {
                "outcome": "transaction",
                "date": f"{pym}-22T13:00:00",
                "amount": -310.00, "currency": "TRY",
                "direction": "expense", "merchant": "GETIR",
                "out_of_period": True,
            },
        },
    ]


_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _mon(m: int) -> str:
    return _MONTHS[m - 1]


def build_fixture(year: int, month: int) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "period": f"{year:04d}-{month:02d}",
        "sender": SENDER,
        "messages": _corpus(year, month),
    }


def expected_try_totals(fixture: dict) -> dict:
    """The TRY-only aggregate the gate's S2 reconciles against.

    Computed from ground truth, never from the store -- an oracle that reads the
    system under test proves nothing.
    """
    period = fixture["period"]
    income = expense = 0.0
    count = 0
    for m in fixture["messages"]:
        e = m["expect"]
        if e.get("outcome") != "transaction":
            continue
        if e.get("currency") != "TRY":
            continue
        if not str(e.get("date", "")).startswith(period):
            continue
        amount = e["amount"]
        count += 1
        if amount > 0:
            income += amount
        else:
            expense += amount
    return {
        "period": period, "currency": "TRY",
        "income": round(income, 2), "expense": round(expense, 2),
        "net": round(income + expense, 2), "count": count,
    }


def seed_db(db_path: Path, fixture: dict) -> int:
    """Insert the fixture's expected transactions straight into a FinanceStore.

    Lets steps 2-4 (analyze / export / chart) be exercised without depending on
    extraction quality.
    """
    from jarvis.finance_store import FinanceStore

    store = FinanceStore(db_path)
    written = 0
    for m in fixture["messages"]:
        e = m["expect"]
        if e.get("outcome") != "transaction":
            continue
        store.upsert_transaction(
            email_uid=m["id"], bank="burgan", date=e["date"],
            amount=e["amount"], currency=e["currency"],
            merchant=e.get("merchant", ""), category=e.get("category", "other"),
            description=m["subject"], raw_subject=m["subject"],
            raw_body_excerpt=m["body"][:500],
        )
        written += 1
    store.close()
    return written


def main() -> int:
    now = datetime.now()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mails", type=Path, help="write the mail fixture JSON here")
    ap.add_argument("--db", type=Path, help="seed transactions into this sessions.db")
    ap.add_argument("--month", default=f"{now.year:04d}-{now.month:02d}",
                    help="period to anchor the corpus on (YYYY-MM, default: this month)")
    args = ap.parse_args()

    if not args.mails and not args.db:
        ap.error("give --mails and/or --db")

    year, month = (int(p) for p in args.month.split("-"))
    fixture = build_fixture(year, month)

    if args.mails:
        args.mails.parent.mkdir(parents=True, exist_ok=True)
        args.mails.write_text(
            json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        n = len(fixture["messages"])
        print(f"[fixture] {n} messages -> {args.mails}")
        print(f"[fixture] expected TRY totals: {expected_try_totals(fixture)}")

    if args.db:
        written = seed_db(args.db, fixture)
        print(f"[fixture] seeded {written} transactions -> {args.db}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
