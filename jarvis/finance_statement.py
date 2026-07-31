"""Bank account statement (PDF) → transactions. Burgan Bank / ON format.

Why this exists: the owner's bank (Burgan, consumer brand **ON**) exports account
history as PDF only — no CSV, no Excel — and their mailbox contains no transaction
notification mails at all (verified live 2026-07-30 across all folders including
spam). So the mail path, while now correctly configured, has no data to read. A
statement file is the owner's real account data, complete, available today, and
needs no credentials.

This is a *statement* parser, deliberately separate from
``jarvis/finance_parser.py`` (which reads notification e-mails). They share the
number format and the categoriser but nothing else: a mail is prose that must be
refused when ambiguous, whereas a statement row is a fixed table cell where the
amount is unambiguous and already signed by the bank.

Format facts, all verified against a real 9-page export rather than assumed:

* Columns are ``Tarih | Açıklama | Tutar | Bakiye``. pdfplumber detects page 1 as
  4 columns and pages 2-8 as 6 — the 6-column rows are the same data with an
  empty leading and trailing cell — so edge-empty cells are stripped before the
  shape is checked. Filtering on ``len(cells) == 4`` alone finds 10 rows out of
  ~90.
* **Amounts carry THREE decimal places**: ``-140,000`` is −140.00 TL, not −140000.
  Confirmed against the balance column: MIGROS ``-847,360`` moves the balance
  3.383,070 → 2.535,710, a difference of exactly 847.36. Turkish grouping (``.``
  thousands, ``,`` decimal) is handled by finance_parser._to_float.
* Sign is explicit in the cell, so direction never has to be inferred.
* A row can straddle a page break, leaving an orphan description fragment with no
  date and no amounts. Such a fragment belongs to the next dated row.
* There is no per-transaction time, only a date.

Idempotency: statements have no message id, so a stable synthetic uid is derived
from (date, amount, balance, description). The balance makes it unique even for
two identical purchases on the same day — re-importing the same PDF converges
instead of duplicating, which is what FinanceStore.upsert_transaction's uid dedup
needs to work.
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from jarvis.finance_parser import ParsedTransaction, _to_float, categorize

_DATE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
_AMOUNT = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{1,3}$")
_PERIOD_LINE = re.compile(
    r"(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})\s*tarihleri"
)

REJECT_NO_AMOUNT = "row_amount_unreadable"
REJECT_NO_DATE = "row_date_unreadable"
REJECT_ZERO = "row_amount_zero"


@dataclass
class StatementParseResult:
    transactions: list[ParsedTransaction] = field(default_factory=list)
    uids: list[str] = field(default_factory=list)          # parallel to transactions
    balances: list[float] = field(default_factory=list)    # parallel; bank's own column
    rejections: Counter = field(default_factory=Counter)
    period_start: str = ""
    period_end: str = ""
    rows_seen: int = 0
    duplicates: int = 0   # page-straddling rows emitted twice by pdfplumber
    error: str = ""


# ── description → merchant ────────────────────────────────────────────────────
# Every pattern below comes from a row that actually appears in the real export.
_MERCHANT_RULES: list[tuple[re.Pattern, int]] = [
    # "POS-Satış-5358..-p.No-186128-620918318699-TABACCO Istanbul TR"
    # The merchant follows the LAST all-digits-and-dash block.
    (re.compile(r"p\.No-\d+-\s*\d+-\s*(.+)$", re.DOTALL), 1),
    # "Gönderen Adı:ÖZGE ALTÜRK SN:6977473105 GönBanka:10 ..."
    (re.compile(r"Gönderen\s*Adı:\s*(.+?)\s*SN:", re.DOTALL | re.IGNORECASE), 1),
    # "İLKER ÖZTÜRK Diğer havale bedeli"
    (re.compile(r"^([A-ZÇĞİÖŞÜ][\w'.-]*(?:\s+[A-ZÇĞİÖŞÜ][\w'.-]*)+)\s+Diğer", re.DOTALL), 1),
]

# Descriptions that are bank fees / mechanics rather than a counterparty.
_FEE_HINTS = re.compile(
    r"KGV|BSMV|komisyon|masraf|ücret|faiz|Kart\s*Yurtdışı|EFT\s*ücret",
    re.IGNORECASE,
)


def _clean(text: str) -> str:
    return " ".join((text or "").replace("\n", " ").split())


def extract_merchant(description: str) -> str:
    flat = _clean(description)
    for pattern, group in _MERCHANT_RULES:
        m = pattern.search(flat)
        if m:
            name = _clean(m.group(group))
            # Trailing country/city noise is part of the POS descriptor, not the
            # name, but keeping it is more informative than a lossy trim -- it is
            # what the owner sees on their own statement.
            if name:
                return name[:70]
    if _FEE_HINTS.search(flat):
        return "BANKA MASRAFI"
    return flat[:70]


def _normalize_row(cells: list[str]) -> list[str]:
    """Strip empty leading/trailing cells so 4-col and 6-col pages look alike."""
    out = [(c or "").strip() for c in cells]
    while out and not out[0]:
        out.pop(0)
    while out and not out[-1]:
        out.pop()
    return out


def _iso_date(token: str) -> str | None:
    m = _DATE.match(token)
    if not m:
        return None
    d, mo, y = (int(g) for g in m.groups())
    try:
        # Statements carry no time-of-day; midnight is honest rather than invented.
        return datetime(y, mo, d).isoformat()
    except ValueError:
        return None


def _row_uid(date_iso: str, amount: float, balance: float | None) -> str:
    """Stable identity for a statement row: (date, amount, running balance).

    The description is deliberately NOT part of the key. A row that straddles a
    page break is emitted by pdfplumber on BOTH pages -- verified in the real
    export, where 18.07.2026 / -151,440 / 1.715,000 appears at the foot of page 4
    (description prefix only) and again at the head of page 5 (with the merchant).
    Keying on the description gave the two copies different ids and the
    transaction was counted twice, throwing the month's expense total out by
    151.44.

    Balance is what makes the key sound: it is the bank's own running total, so it
    changes after every movement -- two genuinely distinct transactions cannot
    share one, not even two identical purchases on the same day.
    """
    raw = f"{date_iso}|{amount:.2f}|{balance if balance is not None else ''}"
    return "stmt:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def parse_rows(rows: "Iterable[list[str]]", result: StatementParseResult | None = None
               ) -> StatementParseResult:
    """Turn extracted table rows into transactions. Pure — no PDF involved.

    Split out from parse_statement so the rules that actually carry risk (the
    three-decimal amounts, the 4-vs-6 column shapes, the page-straddling
    duplicate, orphan description fragments) are testable without generating
    PDFs, which would mean a new dependency for tests alone. What is left in
    parse_statement is thin extraction, validated against the owner's real
    9-page export.
    """
    result = result or StatementParseResult()
    pending_fragment = ""
    seen: dict[str, int] = {}

    for raw in rows:
        cells = _normalize_row(list(raw))
        if not cells:
            continue

        date_iso = _iso_date(cells[0]) if cells else None
        if date_iso is None:
            # No date: either the header row, or a description fragment orphaned
            # by a page break. Keep the fragment; it belongs to the next dated row.
            if len(cells) == 1 and not _AMOUNT.match(cells[0]):
                if cells[0].lower() not in ("tarih", "açıklama", "tutar", "bakiye"):
                    pending_fragment = cells[0]
            continue

        amounts = [c for c in cells[1:] if _AMOUNT.match(c)]
        if not amounts:
            result.rows_seen += 1
            result.rejections[REJECT_NO_AMOUNT] += 1
            pending_fragment = ""
            continue

        result.rows_seen += 1
        amount = _to_float(amounts[0])
        balance = _to_float(amounts[1]) if len(amounts) > 1 else None
        if amount is None:
            result.rejections[REJECT_NO_AMOUNT] += 1
            pending_fragment = ""
            continue
        if amount == 0:
            result.rejections[REJECT_ZERO] += 1
            pending_fragment = ""
            continue

        desc_cells = [c for c in cells[1:] if not _AMOUNT.match(c)]
        description = _clean(" ".join([pending_fragment, *desc_cells]))
        pending_fragment = ""

        merchant = extract_merchant(description)
        direction = "income" if amount > 0 else "expense"
        txn = ParsedTransaction(
            date=date_iso,
            amount=round(amount, 2),
            currency="TRY",   # this export is a single-currency TRY account
            merchant=merchant,
            category=categorize(merchant, description, direction),
            description=description[:200],
            direction=direction,
        )
        uid = _row_uid(date_iso, amount, balance)

        if uid in seen:
            # The page-straddling duplicate. Both halves are real rows from the
            # PDF; keep whichever carries the fuller description, since one half
            # is only the POS prefix and the other has the merchant.
            result.duplicates += 1
            prev = seen[uid]
            if len(description) > len(result.transactions[prev].description):
                result.transactions[prev] = txn
            continue

        seen[uid] = len(result.transactions)
        result.transactions.append(txn)
        result.uids.append(uid)
        result.balances.append(balance if balance is not None else 0.0)

    return result


def parse_statement(path: Path) -> StatementParseResult:
    """Parse a Burgan/ON PDF account statement. Never raises."""
    result = StatementParseResult()
    if not path.exists():
        result.error = f"[ERROR] Dosya bulunamadı: {path}"
        return result
    if path.suffix.lower() != ".pdf":
        result.error = (
            f"[ERROR] Desteklenmeyen dosya türü '{path.suffix}'. Şu an yalnızca "
            "PDF ekstre okunuyor."
        )
        return result
    try:
        import pdfplumber
    except ImportError:
        result.error = "[ERROR] pdfplumber kurulu değil. Çalıştır: pip install pdfplumber"
        return result

    try:
        pdf = pdfplumber.open(str(path))
    except Exception as exc:  # noqa: BLE001 -- a corrupt/encrypted file is a message, not a crash
        result.error = f"[ERROR] PDF açılamadı: {exc}"
        return result

    rows: list[list[str]] = []
    try:
        for page in pdf.pages:
            if not result.period_start:
                m = _PERIOD_LINE.search(page.extract_text() or "")
                if m:
                    result.period_start = _iso_date(m.group(1)) or ""
                    result.period_end = _iso_date(m.group(2)) or ""
            for table in page.extract_tables():
                rows.extend([(c or "") for c in raw] for raw in table)
    finally:
        try:
            pdf.close()
        except Exception:  # noqa: BLE001
            pass

    return parse_rows(rows, result)
