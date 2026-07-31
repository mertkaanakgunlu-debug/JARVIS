"""Finance tool for JARVIS (Faz 16) — Burgan Bank mail extraction + budget tracking.

Main entry point: finance_control(action, **kwargs, settings)

Supported actions:
    sync          — scan Gmail for Burgan Bank mails, extract & save transactions
    summary       — monthly income/expense/net + category breakdown
    recent        — list recent transactions
    top_categories — top N expense categories for a period
    set_budget    — define a monthly budget limit for a category
    budget_status — show spent/limit/% for all budget categories
    export        — write a multi-sheet .xlsx cash-flow analysis workbook
    chart         — generate a Plotly HTML chart for a period (needs plotly)

Every action resolves its reporting period through resolve_period(), and every
aggregate is single-currency (DEFAULT_CURRENCY). Both are deliberate: the MVP
chain sync → summary → export → chart must agree on one date range and one
currency, or the workbook silently contradicts the summary it came from.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from jarvis.finance_store import DEFAULT_CURRENCY

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)

# Upper bound on one sync. Not a display cap (gmail_control's 25-message clamp is
# that, and it is what silently truncated this sync to 25 when it asked for 50) --
# just a sanity ceiling so a pathological mailbox cannot spin forever.
MAX_SYNC_MESSAGES = 500


def resolve_period(year: int = 0, month: int = 0) -> tuple[int, int]:
    """The single place a reporting period is decided.

    The MVP chain (sync -> analyse -> export -> chart) must look at ONE date
    range end to end; otherwise the workbook silently disagrees with the summary
    it was supposedly built from. When the user names no period the answer is the
    current calendar month, and every step is handed that same resolved value.
    """
    now = datetime.now()
    return (year or now.year, month or now.month)


def _import_statement(path_str: str, workspace: "Path", settings: "Settings") -> str:
    """Import a PDF bank statement into the ledger.

    The owner's bank (Burgan / ON) exports PDF only -- no CSV, no Excel -- and the
    mailbox holds no transaction notifications at all, so this is how real account
    data reaches JARVIS today. Unlike the mail path there is no LLM anywhere in
    this flow: a statement row is a table cell with an explicitly signed amount,
    so it is read deterministically or not at all.
    """
    from jarvis import paths
    from jarvis.finance_statement import parse_statement
    from jarvis.finance_store import FinanceStore
    from jarvis.tools.files import _effective_home, _resolve

    # Confined by the SAME guard file_read/file_write use, for two reasons:
    # expanduser() would bypass JARVIS_HOME isolation (tests/test_no_host_path_leak
    # exists precisely to catch that), and an unconfined path here would make
    # import_statement a way around file_read's workspace/home boundary — reading
    # an arbitrary file is a capability, not a detail, even when the tool only
    # looks for transaction rows in it.
    try:
        source = _resolve(path_str, workspace)
    except PermissionError as exc:
        # Name where a statement actually lives. The bare refusal left the model
        # with nothing to correct toward: asked for "indirilenlerdeki Hesap
        # Hareketleri.pdf" it guessed `data/uploads/...`, got "inside a protected
        # directory", and gave up instead of trying the real Downloads path.
        home = _effective_home()
        return (
            f"[ERROR] {exc} Ekstre dosyaları genellikle şurada olur: "
            f"{home / 'Downloads'}. TAM yolu ver, ya da önce "
            f"file_list('{home / 'Downloads'}') ile dosya adını doğrula. "
            "'data/' korumalı dizindir, oradan okunamaz."
        )
    if not source.exists():
        home = _effective_home()
        return (
            f"[ERROR] Dosya bulunamadı: {source}. "
            f"file_list('{home / 'Downloads'}') ile doğru adı bul, sonra tekrar dene."
        )

    result = parse_statement(source)
    if result.error:
        return result.error
    if not result.transactions:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(result.rejections.items()))
        return (
            f"⚠ {source.name} içinde okunabilir işlem satırı bulunamadı "
            f"({result.rows_seen} satır tarandı{'; ' + detail if detail else ''}). "
            "Dosya beklenen Burgan/ON ekstre formatında olmayabilir."
        )

    store = FinanceStore(paths.data_dir() / "sessions.db")
    saved = duplicates = 0
    for txn, uid in zip(result.transactions, result.uids):
        before = store.count_transactions()
        store.upsert_transaction(
            email_uid=uid,
            bank=getattr(settings, "finance_bank", "burgan"),
            date=txn.date, amount=txn.amount, currency=txn.currency,
            merchant=txn.merchant, category=txn.category,
            description=txn.description,
            raw_subject=f"ekstre:{source.name}",
            raw_body_excerpt=txn.description[:500],
        )
        if store.count_transactions() > before:
            saved += 1
        else:
            duplicates += 1

    period = ""
    if result.period_start and result.period_end:
        period = f" · dönem {result.period_start[:10]} → {result.period_end[:10]}"

    income = sum(t.amount for t in result.transactions if t.amount > 0)
    expense = sum(t.amount for t in result.transactions if t.amount < 0)

    lines = [
        f"✅ Ekstre içe aktarıldı: {source.name} — {saved} yeni işlem kaydedildi"
        + (f", {duplicates} zaten kayıtlıydı" if duplicates else "")
        + period,
        f"   Okunan: {len(result.transactions)} işlem · "
        f"Gelir {income:,.2f} / Gider {expense:,.2f} / Net {income + expense:,.2f} "
        f"{DEFAULT_CURRENCY}",
    ]
    if result.duplicates:
        lines.append(
            f"   ℹ {result.duplicates} satır sayfa geçişinde iki kez basılmıştı, "
            "bakiye anahtarıyla tekilleştirildi."
        )
    if result.rejections:
        detail = ", ".join(f"{k}={v}" for k, v in sorted(result.rejections.items()))
        lines.append(f"   ⚠ {sum(result.rejections.values())} satır atlandı ({detail})")
    lines.append(
        "   Excel ve grafik için: finance('export')"
    )
    return "\n".join(lines)


def _sender_clause(sender_filter: str) -> str:
    """Build one Gmail ``from:(a OR b)`` clause from a comma-separated setting.

    A bank's notification sender is frequently NOT its brand domain. Burgan
    Bank's consumer app is branded **ON** and mails from `m.on.com.tr`, so the
    original single-value `from:burgan` could never have matched a real
    notification -- and would have reported an empty mailbox rather than a
    misconfiguration. Verified live against the owner's account, 2026-07-30.
    """
    parts = [p.strip() for p in (sender_filter or "").split(",") if p.strip()]
    if not parts:
        return "from:burgan"
    if len(parts) == 1:
        return f"from:{parts[0]}"
    return "from:(" + " OR ".join(parts) + ")"


def _after_bound(months_back: int) -> str:
    """Gmail ``after:YYYY/MM/DD`` for a months-back window.

    Anchored to the FIRST of the month N-1 months ago, not to "today minus 30N
    days": a sync for "this month" must include the 1st even when run on the 30th.
    """
    now = datetime.now()
    span = max(int(months_back or 1), 1) - 1
    year, month = now.year, now.month - span
    while month <= 0:
        month += 12
        year -= 1
    return f"{year:04d}/{month:02d}/01"


def _get_store(settings: "Settings"):
    from jarvis.finance_store import FinanceStore
    from jarvis import paths
    return FinanceStore(paths.data_dir() / "sessions.db")


def finance_control(
    action: str,
    *,
    year: int = 0,
    month: int = 0,
    category: str = "",
    monthly_limit: float = 0.0,
    alert_threshold_pct: float = 0.8,
    months_back: int = 1,
    n: int = 5,
    output: str = "",
    path: str = "",
    chart_kind: str = "bar",
    workspace: "Path | None" = None,
    settings: "Settings",
) -> str:
    action = action.strip().lower()
    _year, _month = resolve_period(year, month)
    if workspace is None:
        # Same anchor JarvisAgent uses for its own workspace (paths.jarvis_home()),
        # so an export lands inside the isolation root under --profile test.
        from jarvis import paths
        workspace = paths.jarvis_home().resolve()

    # ── sync ─────────────────────────────────────────────────────────────────
    if action == "sync":
        return _sync_burgan(months_back=months_back, settings=settings)

    # ── summary ──────────────────────────────────────────────────────────────
    if action == "summary":
        store = _get_store(settings)
        from jarvis.finance_reporter import format_summary
        summ = store.summary(year=_year, month=_month, currency=DEFAULT_CURRENCY)
        # count_transactions, not len(list_transactions()): the latter is capped
        # at 200 and would report a 250-transaction month as 200.
        total = store.count_transactions(year=_year, month=_month, currency=DEFAULT_CURRENCY)
        out = format_summary(summ, currency=DEFAULT_CURRENCY)
        out += f"\n\n  Toplam işlem: {total}"
        # Say it when money exists that these figures deliberately exclude,
        # rather than presenting a partial total as the whole picture.
        others = [c for c in store.currencies_in_period(year=_year, month=_month)
                  if c != DEFAULT_CURRENCY]
        if others:
            out += (
                f"\n  ℹ Bu özet yalnızca {DEFAULT_CURRENCY} işlemlerini kapsıyor; "
                f"ayrıca {', '.join(others)} işlem(ler)i var (toplamlara katılmadı)."
            )
        return out

    # ── recent ────────────────────────────────────────────────────────────────
    if action == "recent":
        store = _get_store(settings)
        txs = store.recent_transactions(limit=n or 20)
        if not txs:
            return "Kayıtlı işlem yok. `finance('sync')` ile Gmail'den çek."
        lines = [f"Son {len(txs)} işlem:"]
        for t in txs:
            sign = "+" if t["amount"] > 0 else ""
            merchant = t.get("merchant") or t.get("description") or "?"
            lines.append(
                f"  [{t['date'][:10]}]  {merchant:<25}  "
                f"{sign}{t['amount']:>10,.2f} {t['currency']}"
            )
        return "\n".join(lines)

    # ── top_categories ────────────────────────────────────────────────────────
    if action == "top_categories":
        store = _get_store(settings)
        cats = store.top_categories(
            year=_year, month=_month, n=n or 5, currency=DEFAULT_CURRENCY
        )
        if not cats:
            return f"Kayıtlı {DEFAULT_CURRENCY} işlemi yok ({_year}-{_month:02d})."
        from jarvis.finance_reporter import CATEGORY_LABELS
        lines = [f"🏆 {_year}-{_month:02d} En Çok Harcanan Kategoriler ({DEFAULT_CURRENCY}):"]
        for i, c in enumerate(cats, 1):
            label = CATEGORY_LABELS.get(c["category"], c["category"].title())
            # Currency comes from the row now: this line used to hardcode "TRY"
            # while the query had no currency filter, so a USD purchase was
            # printed as Turkish lira.
            lines.append(f"  {i}. {label:<15}  {c['total']:>10,.2f} {c['currency']}")
        return "\n".join(lines)

    # ── set_budget ────────────────────────────────────────────────────────────
    if action == "set_budget":
        if not category:
            return "⚠ category gerekli."
        if monthly_limit <= 0:
            return "⚠ monthly_limit gerekli (pozitif sayı, TRY)."
        store = _get_store(settings)
        store.set_budget(category, monthly_limit, alert_threshold_pct)
        from jarvis.finance_reporter import CATEGORY_LABELS
        label = CATEGORY_LABELS.get(category, category.title())
        return (
            f"✅ Bütçe ayarlandı: {label}  "
            f"Limit: {monthly_limit:,.0f} TRY/ay  "
            f"Uyarı: %{alert_threshold_pct*100:.0f}"
        )

    # ── budget_status ─────────────────────────────────────────────────────────
    if action == "budget_status":
        store = _get_store(settings)
        statuses = store.budget_status(
            year=_year, month=_month, currency=DEFAULT_CURRENCY
        )
        from jarvis.finance_reporter import format_budget_status
        return format_budget_status(statuses, currency=DEFAULT_CURRENCY)

    # ── import_statement ──────────────────────────────────────────────────────
    if action == "import_statement":
        if not path:
            return (
                "⚠ path gerekli — ekstre PDF'inin yolu. "
                "Örn: finance('import_statement', path='C:/Users/.../Hesap Hareketleri.pdf')"
            )
        return _import_statement(path, workspace, settings)

    # ── export ────────────────────────────────────────────────────────────────
    if action == "export":
        store = _get_store(settings)
        from jarvis.tools.workbook import export_cashflow_workbook
        return export_cashflow_workbook(
            store, workspace, year=_year, month=_month,
            currency=DEFAULT_CURRENCY, output=output, chart_kind=chart_kind,
        )

    # ── chart ─────────────────────────────────────────────────────────────────
    if action == "chart":
        store = _get_store(settings)
        summ = store.summary(year=_year, month=_month)
        from jarvis import paths
        finance_dir = paths.resolve(getattr(settings, "finance_data_dir", "data/finance"))
        from jarvis.finance_reporter import generate_chart_html
        path = generate_chart_html(summ, finance_dir)
        if path:
            return f"📊 Grafik oluşturuldu: {path}"
        return "⚠ Grafik oluşturulamadı — plotly kurulu değil (`pip install plotly`)."

    return (
        f"⚠ Bilinmeyen action: '{action}'. Geçerli: sync, import_statement, summary, "
        "recent, top_categories, set_budget, budget_status, export, chart"
    )


def _sync_burgan(months_back: int, settings: "Settings") -> str:
    """Scan Gmail for bank notification mails and extract transactions.

    Rewritten 2026-07-30. Three defects the old version had, all silent:

      * ``months_back`` was accepted, threaded down here, and then **never used**
        -- the Gmail query was a bare ``from:burgan`` over the whole mailbox. It
        now becomes a real ``after:`` bound, so sync and export cannot look at
        different date ranges.
      * It searched via ``gmail_control`` (human-readable output) and recovered
        message ids, subjects and bodies by **regex over the display string**.
        That coupling broke twice already (BUG-15). It now uses
        gmail.search_messages(), which returns structured fields.
      * It then issued a **second** ``read`` call per message to re-fetch data
        the search had already downloaded and discarded -- 2N API calls. Now N.

    Also: a skipped mail is no longer an anonymous tally. Rejections are counted
    by reason and summarised, because "12 atlandı" tells the owner nothing while
    "3 not a transaction, 1 no date" points straight at the problem.
    """
    import asyncio
    import concurrent.futures
    from collections import Counter

    def _run_coro(coro):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    async def _do_sync():
        from jarvis import paths
        from jarvis.finance_extractor import extract_transaction
        from jarvis.finance_parser import ParsedTransaction
        from jarvis.finance_store import FinanceStore
        from jarvis.tools.gmail import search_messages

        store = FinanceStore(paths.data_dir() / "sessions.db")
        sender_filter = getattr(settings, "finance_sender_filter", "burgan")
        after = _after_bound(months_back)
        query = f"{_sender_clause(sender_filter)} after:{after}"

        try:
            messages = search_messages(query, settings, limit=MAX_SYNC_MESSAGES)
        except RuntimeError as exc:
            # Auth/credential problems are the common case here and must be
            # actionable, not swallowed into "0 kaydedildi".
            return f"[ERROR] {exc}"

        if not messages:
            return f"📭 Banka bildirimi bulunamadı (filtre: {query})"

        saved = 0
        duplicates = 0
        rejections: Counter[str] = Counter()
        for msg in messages:
            outcome = await extract_transaction(
                msg["subject"], msg["body"], settings,
                date_header=msg.get("date_raw", ""),
            )
            if not isinstance(outcome, ParsedTransaction):
                rejections[outcome.reason] += 1
                continue

            try:
                existed = store.count_transactions()
                store.upsert_transaction(
                    email_uid=msg["id"],
                    bank=getattr(settings, "finance_bank", "burgan"),
                    date=outcome.date,
                    amount=outcome.amount,
                    currency=outcome.currency,
                    merchant=outcome.merchant,
                    category=outcome.category,
                    description=outcome.description,
                    raw_subject=msg["subject"],
                    raw_body_excerpt=msg["body"][:500],
                )
                if store.count_transactions() > existed:
                    saved += 1
                else:
                    duplicates += 1
            except Exception as exc:
                logger.debug("Upsert error: %s", exc)
                rejections["store_error"] += 1

        parts = [f"✅ Senkronizasyon tamamlandı: {saved} işlem kaydedildi"]
        if duplicates:
            parts.append(f"{duplicates} zaten kayıtlıydı")
        if rejections:
            detail = ", ".join(f"{reason}={n}" for reason, n in sorted(rejections.items()))
            parts.append(f"{sum(rejections.values())} atlandı ({detail})")
        parts.append(f"toplam {len(messages)} mail tarandı, tarih ≥ {after}")
        return " · ".join(parts)

    try:
        return _run_coro(_do_sync())
    except Exception as exc:
        return f"⚠ Sync hatası: {exc}"
