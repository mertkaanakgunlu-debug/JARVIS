"""Finance tool for JARVIS (Faz 16) — Burgan Bank mail extraction + budget tracking.

Main entry point: finance_control(action, **kwargs, settings)

Supported actions:
    sync          — scan Gmail for Burgan Bank mails, extract & save transactions
    summary       — monthly income/expense/net + category breakdown
    recent        — list recent transactions
    top_categories — top N expense categories for a period
    set_budget    — define a monthly budget limit for a category
    budget_status — show spent/limit/% for all budget categories
    chart         — generate a Plotly HTML chart for a period
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jarvis.config import Settings

logger = logging.getLogger(__name__)


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
    settings: "Settings",
) -> str:
    action = action.strip().lower()
    now = datetime.now()
    _year = year or now.year
    _month = month or now.month

    # ── sync ─────────────────────────────────────────────────────────────────
    if action == "sync":
        return _sync_burgan(months_back=months_back, settings=settings)

    # ── summary ──────────────────────────────────────────────────────────────
    if action == "summary":
        store = _get_store(settings)
        from jarvis.finance_reporter import format_summary
        summ = store.summary(year=_year, month=_month)
        total = len(store.list_transactions(year=_year, month=_month))
        return format_summary(summ) + f"\n\n  Toplam işlem: {total}"

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
        cats = store.top_categories(year=_year, month=_month, n=n or 5)
        if not cats:
            return f"Kayıtlı işlem yok ({_year}-{_month:02d})."
        from jarvis.finance_reporter import CATEGORY_LABELS
        lines = [f"🏆 {_year}-{_month:02d} En Çok Harcanan Kategoriler:"]
        for i, c in enumerate(cats, 1):
            label = CATEGORY_LABELS.get(c["category"], c["category"].title())
            lines.append(f"  {i}. {label:<15}  {c['total']:>10,.2f} TRY")
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
        statuses = store.budget_status(year=_year, month=_month)
        from jarvis.finance_reporter import format_budget_status
        return format_budget_status(statuses)

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
        f"⚠ Bilinmeyen action: '{action}'. "
        "Geçerli: sync, summary, recent, top_categories, set_budget, budget_status, chart"
    )


def _sync_burgan(months_back: int, settings: "Settings") -> str:
    """Scan Gmail for Burgan Bank mails and extract transactions."""
    import asyncio
    import concurrent.futures

    def _run_coro(coro):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()

    async def _do_sync():
        from jarvis.tools.gmail import gmail_control
        from jarvis.finance_extractor import extract_transaction
        from jarvis.finance_store import FinanceStore
        from jarvis import paths

        store = FinanceStore(paths.data_dir() / "sessions.db")
        sender_filter = getattr(settings, "finance_sender_filter", "burgan")
        query = f"from:{sender_filter}"

        # Use Gmail search tool to find Burgan messages
        raw = gmail_control(
            action="search",
            query=query,
            max_results=50,
            settings=settings,
        )

        # Parse out message IDs from the result lines. BUG-15: gmail.py's
        # _fmt_message() actually emits "• [id]  Subject" (bullet prefix) --
        # this regex required plain leading whitespace before "[", which never
        # matched, so msg_ids was always empty and sync silently found nothing.
        import re
        msg_ids = re.findall(r"^•\s*\[([A-Za-z0-9]+)\]", raw, re.MULTILINE)

        if not msg_ids:
            return f"📭 Burgan bildirimi bulunamadı (filtre: {query})"

        saved = 0
        skipped = 0
        for mid in msg_ids[:50]:
            full = gmail_control(
                action="read",
                message_id=mid,
                settings=settings,
            )
            # Extract subject + body from the read output. Same root cause as
            # the msg_ids regex above: gmail.py's actual format is
            # "• [id]  Subject" / "  From: ...\n\n{body}", not "Konu: .../---".
            subject_match = re.search(r"^•\s*\[[A-Za-z0-9]+\]\s+(.+)$", full, re.MULTILINE)
            subject = subject_match.group(1).strip() if subject_match else ""
            # Body starts after the blank line that separates it from the headers
            body_split = full.split("\n\n", 1)
            body = body_split[1].strip()[:2000] if len(body_split) > 1 else full[:2000]

            tx = await extract_transaction(subject, body, settings)
            if tx is None:
                skipped += 1
                continue

            try:
                store.upsert_transaction(
                    email_uid=mid,
                    bank=getattr(settings, "finance_bank", "burgan"),
                    date=tx.date,
                    amount=tx.amount,
                    currency=tx.currency,
                    merchant=tx.merchant,
                    category=tx.category,
                    description=tx.description,
                    raw_subject=subject,
                    raw_body_excerpt=body[:500],
                )
                saved += 1
            except Exception as exc:
                logger.debug("Upsert error: %s", exc)
                skipped += 1

        return (
            f"✅ Senkronizasyon tamamlandı: {saved} işlem kaydedildi, "
            f"{skipped} atlandı (toplam {len(msg_ids)} mail tarandı)"
        )

    try:
        return _run_coro(_do_sync())
    except Exception as exc:
        return f"⚠ Sync hatası: {exc}"
