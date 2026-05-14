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
from datetime import datetime
from pathlib import Path


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

    def summary(self, *, year: int, month: int) -> dict:
        """Return aggregated income, expenses, net, and category breakdown."""
        prefix = f"{year:04d}-{month:02d}"
        rows = self._conn.execute(
            "SELECT category, SUM(amount) AS total FROM transactions "
            "WHERE date LIKE ? GROUP BY category",
            (f"{prefix}%",),
        ).fetchall()
        income = 0.0
        expenses = 0.0
        by_category: dict[str, float] = {}
        for r in rows:
            total = r["total"] or 0.0
            cat = r["category"] or "other"
            by_category[cat] = total
            if total > 0:
                income += total
            else:
                expenses += total
        return {
            "year": year,
            "month": month,
            "income": income,
            "expenses": expenses,
            "net": income + expenses,
            "by_category": by_category,
        }

    def top_categories(self, *, year: int, month: int, n: int = 5) -> list[dict]:
        """Return top N expense categories by absolute amount (most spent first)."""
        prefix = f"{year:04d}-{month:02d}"
        rows = self._conn.execute(
            "SELECT category, SUM(amount) AS total FROM transactions "
            "WHERE date LIKE ? AND amount < 0 GROUP BY category "
            "ORDER BY total ASC LIMIT ?",
            (f"{prefix}%", n),
        ).fetchall()
        return [{"category": r["category"], "total": r["total"]} for r in rows]

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

    def budget_status(self, *, year: int, month: int) -> list[dict]:
        """Return each budget category with spent/limit/pct and over_threshold flag."""
        budgets = {b["category"]: b for b in self.get_budgets()}
        if not budgets:
            return []
        prefix = f"{year:04d}-{month:02d}"
        rows = self._conn.execute(
            "SELECT category, SUM(amount) AS spent FROM transactions "
            "WHERE date LIKE ? AND amount < 0 "
            "AND category IN ({}) GROUP BY category".format(
                ",".join("?" * len(budgets))
            ),
            [f"{prefix}%"] + list(budgets.keys()),
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
