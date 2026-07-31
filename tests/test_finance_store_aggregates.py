"""FinanceStore aggregates: sign-correct and currency-scoped.

Two bugs found in the owner's 2026-07-30 code review. Both produced confidently
wrong numbers rather than errors, which is the worst failure mode for a ledger --
nothing looked broken.

  1. summary() ran ``SUM(amount) GROUP BY category`` and then decided whether
     each category's NET was income or expense. A category containing both a
     salary credit and a fee debit collapsed to one signed number, and the
     smaller side disappeared from the month entirely.

  2. Nothing filtered by currency, anywhere: summary(), top_categories() and
     budget_status() added TRY, USD and EUR together, and the callers printed the
     result as "TRY". budget_status() was the most damaging -- it counted foreign
     spend against a lira limit, under-reporting overspending.

The mixed-sign-category and multi-currency tests here fail loudly if either
regresses; several assert the exact arithmetic the old code got wrong, so they
cannot pass by coincidence.
"""
from __future__ import annotations

import pytest

from jarvis.finance_store import FinanceStore


@pytest.fixture
def store(tmp_path):
    s = FinanceStore(tmp_path / "sessions.db")
    yield s
    s.close()


def _add(store, uid, date, amount, currency="TRY", category="other", merchant=""):
    store.upsert_transaction(
        email_uid=uid, bank="burgan", date=date, amount=amount,
        currency=currency, merchant=merchant, category=category,
    )


# ── bug 1: netting before classifying ─────────────────────────────────────────

def test_mixed_sign_category_does_not_swallow_the_smaller_side(store):
    """The exact shape that broke: one category, one credit, one debit.

    Old behavior: +42500 and -300 netted to +42200, classified as income by its
    sign, and the 300 expense vanished -> income 42200 / expenses 0.
    """
    _add(store, "a", "2026-07-10T03:00:00", 42500.00, category="salary")
    _add(store, "b", "2026-07-11T09:00:00", -300.00, category="salary")

    s = store.summary(year=2026, month=7)

    assert s["income"] == pytest.approx(42500.00)
    assert s["expenses"] == pytest.approx(-300.00)
    assert s["net"] == pytest.approx(42200.00)


def test_mixed_sign_category_is_reported_split_as_well_as_net(store):
    _add(store, "a", "2026-07-10T03:00:00", 1000.00, category="transfer")
    _add(store, "b", "2026-07-11T09:00:00", -250.00, category="transfer")

    split = store.summary(year=2026, month=7)["by_category_split"]["transfer"]

    assert split["income"] == pytest.approx(1000.00)
    assert split["expense"] == pytest.approx(-250.00)
    assert split["net"] == pytest.approx(750.00)
    assert split["count"] == 2


def test_by_category_keeps_its_historical_signed_net_meaning(store):
    """finance_reporter.format_summary consumes by_category unchanged; breaking
    its shape would break the CLI's existing finance output."""
    _add(store, "a", "2026-07-10T03:00:00", 1000.00, category="transfer")
    _add(store, "b", "2026-07-11T09:00:00", -250.00, category="transfer")

    assert store.summary(year=2026, month=7)["by_category"]["transfer"] == pytest.approx(750.0)


# ── bug 2: currency cross-summing ─────────────────────────────────────────────

def test_summary_excludes_other_currencies(store):
    _add(store, "try", "2026-07-07T14:00:00", -250.75, currency="TRY")
    _add(store, "usd", "2026-07-14T10:00:00", -120.50, currency="USD")

    tr = store.summary(year=2026, month=7, currency="TRY")
    us = store.summary(year=2026, month=7, currency="USD")

    assert tr["expenses"] == pytest.approx(-250.75)
    assert tr["count"] == 1
    assert us["expenses"] == pytest.approx(-120.50)
    assert tr["currency"] == "TRY"


def test_summary_defaults_to_try(store):
    _add(store, "usd", "2026-07-14T10:00:00", -120.50, currency="USD")

    assert store.summary(year=2026, month=7)["count"] == 0


def test_currencies_in_period_reports_what_is_actually_there(store):
    """So a caller can SAY a foreign amount exists rather than dropping it
    silently from a TRY-only total."""
    _add(store, "try", "2026-07-07T14:00:00", -250.75, currency="TRY")
    _add(store, "usd", "2026-07-14T10:00:00", -120.50, currency="USD")

    assert store.currencies_in_period(year=2026, month=7) == ["TRY", "USD"]


def test_summary_by_currency_never_cross_sums(store):
    _add(store, "try", "2026-07-07T14:00:00", -250.75, currency="TRY")
    _add(store, "usd", "2026-07-14T10:00:00", -120.50, currency="USD")

    blocks = store.summary_by_currency(year=2026, month=7)

    assert set(blocks) == {"TRY", "USD"}
    assert blocks["TRY"]["expenses"] == pytest.approx(-250.75)
    assert blocks["USD"]["expenses"] == pytest.approx(-120.50)


def test_top_categories_is_currency_scoped_and_labels_itself(store):
    _add(store, "try", "2026-07-07T14:00:00", -250.75, currency="TRY", category="food")
    _add(store, "usd", "2026-07-14T10:00:00", -9999.00, currency="USD", category="shopping")

    cats = store.top_categories(year=2026, month=7, n=5)

    assert [c["category"] for c in cats] == ["food"]
    assert cats[0]["currency"] == "TRY"


def test_budget_status_ignores_foreign_currency_spend(store):
    """A 120 USD purchase must not consume 120 lira of a TRY budget."""
    store.set_budget("shopping", 1000.0, 0.8)
    _add(store, "usd", "2026-07-14T10:00:00", -900.00, currency="USD", category="shopping")

    statuses = store.budget_status(year=2026, month=7)

    assert statuses[0]["spent"] == pytest.approx(0.0)
    assert statuses[0]["over_threshold"] is False


def test_budget_status_still_counts_try_spend(store):
    store.set_budget("food", 1000.0, 0.8)
    _add(store, "t", "2026-07-14T10:00:00", -900.00, currency="TRY", category="food")

    statuses = store.budget_status(year=2026, month=7)

    assert statuses[0]["spent"] == pytest.approx(900.0)
    assert statuses[0]["over_threshold"] is True


# ── export-safe primitives ────────────────────────────────────────────────────

def test_iter_transactions_is_not_capped_at_the_display_limit(store):
    """list_transactions defaults to limit=200; a 250-transaction month exported
    through it would lose 50 rows with nothing reporting the loss."""
    for i in range(250):
        _add(store, f"u{i}", f"2026-07-{(i % 28) + 1:02d}T10:00:{i % 60:02d}", -10.0)

    assert len(store.list_transactions(year=2026, month=7)) == 200
    assert len(list(store.iter_transactions(year=2026, month=7))) == 250
    assert store.count_transactions(year=2026, month=7) == 250


def test_iter_transactions_yields_every_row_exactly_once(store):
    """Keyset pagination must not skip or duplicate across batch boundaries --
    including when many rows share a date."""
    for i in range(120):
        _add(store, f"u{i}", "2026-07-15T10:00:00", -1.0 * (i + 1))

    seen = [t["id"] for t in store.iter_transactions(year=2026, month=7, batch=25)]

    assert len(seen) == 120
    assert len(set(seen)) == 120


def test_iter_transactions_includes_every_currency_by_default(store):
    """The transactions sheet must show a foreign row even though the TRY
    aggregates exclude it."""
    _add(store, "try", "2026-07-07T14:00:00", -250.75, currency="TRY")
    _add(store, "usd", "2026-07-14T10:00:00", -120.50, currency="USD")

    got = {t["currency"] for t in store.iter_transactions(year=2026, month=7)}

    assert got == {"TRY", "USD"}


def test_category_breakdown_is_full_not_top_n(store):
    """top_categories(n=5) is a top-N EXPENSE view; a workbook built on it would
    drop the tail and all income."""
    for i in range(8):
        _add(store, f"e{i}", f"2026-07-0{i + 1}T10:00:00", -(i + 1) * 100.0,
             category=f"cat{i}")
    _add(store, "inc", "2026-07-10T03:00:00", 5000.0, category="salary")

    breakdown = store.category_breakdown(year=2026, month=7)

    assert len(breakdown) == 9, "8 expense categories + salary"
    salary = next(b for b in breakdown if b["category"] == "salary")
    assert salary["income"] == pytest.approx(5000.0)


def test_period_filter_excludes_other_months(store):
    _add(store, "jul", "2026-07-07T14:00:00", -250.75)
    _add(store, "jun", "2026-06-22T13:00:00", -310.00)

    assert store.count_transactions(year=2026, month=7) == 1
    assert store.summary(year=2026, month=7)["expenses"] == pytest.approx(-250.75)
