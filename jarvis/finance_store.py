"""Finance store for JARVIS (Faz 16) — SQLite CRUD for transactions and budgets.

Tables live inside the shared data/sessions.db (WAL mode, own connection).

Schemas:
  transactions — one row per bank notification (deduplicated by email_uid)
  budgets      — per-category monthly limit + alert threshold
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

# Every aggregate in this module is single-currency by construction. TRY is the
# default because it is this user's home currency and the MVP's declared
# acceptance scope; nothing here ever adds two currencies together.
DEFAULT_CURRENCY = "TRY"


_FINANCE_TABLES = """
CREATE TABLE IF NOT EXISTS transactions (
    id                TEXT PRIMARY KEY,
    email_uid         TEXT UNIQUE,
    bank              TEXT NOT NULL,
    date              TEXT NOT NULL,
    amount            REAL NOT NULL,
    currency          TEXT DEFAULT 'TRY',
    merchant          TEXT,
    category          TEXT,
    description       TEXT,
    raw_subject       TEXT,
    raw_body_excerpt  TEXT,
    extracted_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tx_date     ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_tx_category ON transactions(category);
CREATE INDEX IF NOT EXISTS idx_tx_bank     ON transactions(bank);

CREATE TABLE IF NOT EXISTS budgets (
    category            TEXT PRIMARY KEY,
    monthly_limit       REAL NOT NULL,
    alert_threshold_pct REAL DEFAULT 0.8
);
"""


class FinanceStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = self._connect(db_path)

    def _connect(self, path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_FINANCE_TABLES)
        return conn

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    # ── Transactions ──────────────────────────────────────────────────────────

    def upsert_transaction(
        self,
        *,
        email_uid: str = "",
        bank: str = "burgan",
        date: str,
        amount: float,
        currency: str = "TRY",
        merchant: str = "",
        category: str = "other",
        description: str = "",
        raw_subject: str = "",
        raw_body_excerpt: str = "",
    ) -> str:
        """Insert a transaction; if email_uid already exists, skip (return existing id)."""
        with self._lock:
            if email_uid:
                row = self._conn.execute(
                    "SELECT id FROM transactions WHERE email_uid = ?", (email_uid,)
                ).fetchone()
                if row:
                    return row["id"]
            tid = uuid.uuid4().hex[:8]
            now = datetime.now().isoformat()
            self._conn.execute(
                """INSERT INTO transactions
                   (id, email_uid, bank, date, amount, currency, merchant, category,
                    description, raw_subject, raw_body_excerpt, extracted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (tid, email_uid or None, bank, date, amount, currency,
                 merchant, category, description, raw_subject,
                 raw_body_excerpt[:500], now),
            )
            return tid

    def list_transactions(
        self,
        *,
        year: int | None = None,
        month: int | None = None,
        category: str | None = None,
        bank: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        """Return transactions filtered by optional year/month/category/bank."""
        clauses: list[str] = []
        params: list = []
        if year and month:
            prefix = f"{year:04d}-{month:02d}"
            clauses.append("date LIKE ?")
            params.append(f"{prefix}%")
        elif year:
            clauses.append("date LIKE ?")
            params.append(f"{year:04d}%")
        if category:
            clauses.append("category = ?")
            params.append(category)
        if bank:
            clauses.append("bank = ?")
            params.append(bank)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM transactions {where} ORDER BY date DESC LIMIT ?",
            params + [limit],
        ).fetchall()
        return [dict(r) for r in rows]

    def summary(self, *, year: int, month: int, currency: str = DEFAULT_CURRENCY) -> dict:
        """Aggregated income, expenses, net and category breakdown for ONE currency.

        Two bugs were fixed here on 2026-07-30; both produced confidently wrong
        totals rather than errors, which is why they survived so long:

        1. **Netting before classifying.** The old query was
           ``SUM(amount) GROUP BY category``, and the resulting per-category NET
           was then classified as income or expense by its sign. A category
           holding a +42.500 salary and a -300 fee collapsed into one +42.200 row
           counted entirely as income -- the expense simply vanished from the
           month. Income and expense are now summed separately, per row, by sign.

        2. **No currency filter at all.** TRY, USD and EUR amounts were added
           together into a single number that was then labelled "TRY". This
           method now aggregates exactly one currency and says which in its
           return value; see summary_by_currency() for every currency at once.
        """
        prefix = f"{year:04d}-{month:02d}"
        agg = self._conn.execute(
            "SELECT "
            "  COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) AS income, "
            "  COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) AS expenses, "
            "  COUNT(*) AS n "
            "FROM transactions WHERE date LIKE ? AND currency = ?",
            (f"{prefix}%", currency),
        ).fetchone()
        rows = self._conn.execute(
            "SELECT category, "
            "  COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) AS income, "
            "  COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) AS expense, "
            "  SUM(amount) AS net, COUNT(*) AS n "
            "FROM transactions WHERE date LIKE ? AND currency = ? "
            "GROUP BY category",
            (f"{prefix}%", currency),
        ).fetchall()

        income = agg["income"] or 0.0
        expenses = agg["expenses"] or 0.0
        # by_category keeps its historical meaning (signed net per category) so
        # finance_reporter.format_summary keeps working unchanged; the separated
        # figures live alongside it for callers that need them.
        by_category = {(r["category"] or "other"): (r["net"] or 0.0) for r in rows}
        return {
            "year": year,
            "month": month,
            "currency": currency,
            "income": round(income, 2),
            "expenses": round(expenses, 2),
            "net": round(income + expenses, 2),
            "count": agg["n"] or 0,
            "by_category": by_category,
            "by_category_split": {
                (r["category"] or "other"): {
                    "income": round(r["income"] or 0.0, 2),
                    "expense": round(r["expense"] or 0.0, 2),
                    "net": round(r["net"] or 0.0, 2),
                    "count": r["n"] or 0,
                }
                for r in rows
            },
        }

    def currencies_in_period(self, *, year: int, month: int) -> list[str]:
        """Every currency with at least one transaction in the period.

        Lets callers SAY that a non-TRY amount exists instead of silently
        dropping it from a TRY-only total.
        """
        prefix = f"{year:04d}-{month:02d}"
        rows = self._conn.execute(
            "SELECT DISTINCT currency FROM transactions WHERE date LIKE ? "
            "ORDER BY currency",
            (f"{prefix}%",),
        ).fetchall()
        return [r["currency"] or DEFAULT_CURRENCY for r in rows]

    def summary_by_currency(self, *, year: int, month: int) -> dict[str, dict]:
        """One summary block per currency present -- never a cross-currency sum."""
        return {
            cur: self.summary(year=year, month=month, currency=cur)
            for cur in self.currencies_in_period(year=year, month=month)
        }

    def top_categories(
        self, *, year: int, month: int, n: int = 5, currency: str = DEFAULT_CURRENCY,
    ) -> list[dict]:
        """Top N expense categories by amount spent (most spent first).

        The per-row ``amount < 0`` filter was already correct here, but there was
        no currency filter while jarvis/tools/finance.py labelled the output
        "TRY" unconditionally -- so a USD purchase was printed as Turkish lira.
        """
        prefix = f"{year:04d}-{month:02d}"
        rows = self._conn.execute(
            "SELECT category, SUM(amount) AS total, COUNT(*) AS n "
            "FROM transactions "
            "WHERE date LIKE ? AND currency = ? AND amount < 0 "
            "GROUP BY category ORDER BY total ASC LIMIT ?",
            (f"{prefix}%", currency, n),
        ).fetchall()
        return [
            {"category": r["category"], "total": r["total"],
             "count": r["n"], "currency": currency}
            for r in rows
        ]

    def category_breakdown(
        self, *, year: int, month: int, currency: str = DEFAULT_CURRENCY,
    ) -> list[dict]:
        """FULL category breakdown -- every category, income and expense split.

        Distinct from top_categories(), which is a top-N *expense* view. A
        workbook built from that would silently omit both the tail categories and
        all income, so the export path uses this instead.
        """
        prefix = f"{year:04d}-{month:02d}"
        rows = self._conn.execute(
            "SELECT category, "
            "  COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) AS income, "
            "  COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) AS expense, "
            "  SUM(amount) AS net, COUNT(*) AS n "
            "FROM transactions WHERE date LIKE ? AND currency = ? "
            "GROUP BY category ORDER BY expense ASC",
            (f"{prefix}%", currency),
        ).fetchall()
        return [
            {
                "category": r["category"] or "other",
                "income": round(r["income"] or 0.0, 2),
                "expense": round(r["expense"] or 0.0, 2),
                "net": round(r["net"] or 0.0, 2),
                "count": r["n"] or 0,
                "currency": currency,
            }
            for r in rows
        ]

    def daily_flow(
        self, *, year: int, month: int, currency: str = DEFAULT_CURRENCY,
        fill_period: bool = False,
    ) -> list[dict]:
        """Per-day income/expense/net and a running balance, oldest first.

        This is what "para akışı" (cash *flow*) actually means -- a single
        month-total row is a position, not a flow, and charts as one bar.

        ``fill_period`` emits EVERY day of the month, zero where nothing moved.
        Off by default (a caller reading raw activity wants only real days), but
        the chart path turns it on, and the reason is correctness rather than
        cosmetics: with gaps omitted the x-axis is **categorical, not temporal** —
        a three-day gap and a one-day gap render the same width, so the picture
        misstates when money actually moved. The owner noticed the symptom
        (2026-07-30: "30 gün yok, 16 bar görüyorum"). A zero day means "no
        recorded movement", which for a bank statement is a fact, not a guess.
        """
        prefix = f"{year:04d}-{month:02d}"
        rows = self._conn.execute(
            "SELECT substr(date, 1, 10) AS day, "
            "  COALESCE(SUM(CASE WHEN amount > 0 THEN amount END), 0) AS income, "
            "  COALESCE(SUM(CASE WHEN amount < 0 THEN amount END), 0) AS expense, "
            "  SUM(amount) AS net, COUNT(*) AS n "
            "FROM transactions WHERE date LIKE ? AND currency = ? "
            "GROUP BY day ORDER BY day ASC",
            (f"{prefix}%", currency),
        ).fetchall()
        by_day = {r["day"]: r for r in rows}
        if fill_period:
            import calendar as _calendar
            days = [
                f"{prefix}-{d:02d}"
                for d in range(1, _calendar.monthrange(year, month)[1] + 1)
            ]
        else:
            days = [r["day"] for r in rows]

        out: list[dict] = []
        running = 0.0
        for day in days:
            r = by_day.get(day)
            net = (r["net"] if r else 0.0) or 0.0
            running += net
            out.append({
                "day": day,
                "income": round((r["income"] if r else 0.0) or 0.0, 2),
                "expense": round((r["expense"] if r else 0.0) or 0.0, 2),
                "net": round(net, 2),
                "running_net": round(running, 2),
                "count": (r["n"] if r else 0) or 0,
                "currency": currency,
            })
        return out

    def periods_with_data(self, *, limit: int = 12) -> list[str]:
        """YYYY-MM strings that actually hold transactions, newest first.

        Lets an "empty period" error name the periods that are NOT empty. Without
        it the only available advice is "run sync first", which is actively
        misleading when sync has already succeeded -- a live qwen3:8b run read that
        message and looped on sync instead of correcting its month.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT substr(date, 1, 7) AS period FROM transactions "
            "ORDER BY period DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [r["period"] for r in rows if r["period"]]

    def count_transactions(
        self, *, year: int | None = None, month: int | None = None,
        currency: str | None = None, bank: str | None = None,
    ) -> int:
        """Real row count for the filter -- NOT capped by a display limit.

        The workbook needs to know whether it wrote every transaction; comparing
        against len(list_transactions()) could only ever confirm the cap.
        """
        clauses, params = self._filter_clauses(
            year=year, month=month, currency=currency, bank=bank
        )
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        row = self._conn.execute(
            f"SELECT COUNT(*) AS n FROM transactions {where}", params
        ).fetchone()
        return int(row["n"] or 0)

    def _filter_clauses(
        self, *, year: int | None = None, month: int | None = None,
        category: str | None = None, currency: str | None = None,
        bank: str | None = None,
    ) -> tuple[list[str], list]:
        clauses: list[str] = []
        params: list = []
        if year and month:
            clauses.append("date LIKE ?")
            params.append(f"{year:04d}-{month:02d}%")
        elif year:
            clauses.append("date LIKE ?")
            params.append(f"{year:04d}%")
        if category:
            clauses.append("category = ?")
            params.append(category)
        if currency:
            clauses.append("currency = ?")
            params.append(currency)
        if bank:
            clauses.append("bank = ?")
            params.append(bank)
        return clauses, params

    def iter_transactions(
        self, *, year: int | None = None, month: int | None = None,
        category: str | None = None, currency: str | None = None,
        bank: str | None = None, batch: int = 500,
    ) -> "Iterator[dict]":
        """Every matching transaction, oldest first, with NO limit.

        list_transactions() defaults to ``limit=200``, which is right for a chat
        reply and wrong for an export: a month with 201 transactions would be
        silently truncated in the workbook with nothing anywhere saying so.
        Keyset pagination on (date, id) rather than OFFSET, so a concurrent
        insert cannot make a row be skipped or repeated.

        Note ``currency=None`` means EVERY currency on purpose -- the
        transactions sheet must show a non-TRY row even though the TRY
        aggregates exclude it.
        """
        last_key: tuple[str, str] | None = None
        while True:
            clauses, params = self._filter_clauses(
                year=year, month=month, category=category,
                currency=currency, bank=bank,
            )
            if last_key is not None:
                clauses.append("(date > ? OR (date = ? AND id > ?))")
                params.extend([last_key[0], last_key[0], last_key[1]])
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = self._conn.execute(
                f"SELECT * FROM transactions {where} "
                "ORDER BY date ASC, id ASC LIMIT ?",
                params + [batch],
            ).fetchall()
            if not rows:
                return
            for r in rows:
                yield dict(r)
            last_key = (rows[-1]["date"], rows[-1]["id"])
            if len(rows) < batch:
                return

    def recent_transactions(self, *, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM transactions ORDER BY date DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Budgets ───────────────────────────────────────────────────────────────

    def set_budget(self, category: str, monthly_limit: float, alert_threshold_pct: float = 0.8) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO budgets (category, monthly_limit, alert_threshold_pct)
                   VALUES (?, ?, ?)
                   ON CONFLICT(category) DO UPDATE SET
                     monthly_limit = excluded.monthly_limit,
                     alert_threshold_pct = excluded.alert_threshold_pct""",
                (category, monthly_limit, alert_threshold_pct),
            )

    def get_budgets(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM budgets ORDER BY category").fetchall()
        return [dict(r) for r in rows]

    def budget_status(
        self, *, year: int, month: int, currency: str = DEFAULT_CURRENCY,
    ) -> list[dict]:
        """Each budget category with spent/limit/pct and over_threshold flag.

        Currency filter added 2026-07-30: budgets are denominated in TRY
        (set_budget's own message says so) but this counted expenses in ANY
        currency against them, so a 120 USD purchase consumed 120 lira of a TRY
        budget. Wrong in the direction that under-reports overspending.
        """
        budgets = {b["category"]: b for b in self.get_budgets()}
        if not budgets:
            return []
        prefix = f"{year:04d}-{month:02d}"
        rows = self._conn.execute(
            "SELECT category, SUM(amount) AS spent FROM transactions "
            "WHERE date LIKE ? AND currency = ? AND amount < 0 "
            "AND category IN ({}) GROUP BY category".format(
                ",".join("?" * len(budgets))
            ),
            [f"{prefix}%", currency] + list(budgets.keys()),
        ).fetchall()
        spent_map = {r["category"]: abs(r["spent"] or 0.0) for r in rows}
        result = []
        for cat, b in budgets.items():
            spent = spent_map.get(cat, 0.0)
            limit = b["monthly_limit"]
            pct = spent / limit if limit > 0 else 0.0
            result.append({
                "category": cat,
                "spent": spent,
                "limit": limit,
                "pct": pct,
                "over_threshold": pct >= b["alert_threshold_pct"],
                "alert_threshold_pct": b["alert_threshold_pct"],
            })
        return sorted(result, key=lambda x: -x["pct"])
