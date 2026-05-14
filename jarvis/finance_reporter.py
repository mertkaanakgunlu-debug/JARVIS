"""Finance reporter for JARVIS (Faz 16) — text summaries and optional charts.

Generates monthly financial summaries as formatted text.
Plotly HTML charts are generated when plotly is installed.
LaTeX report generation chains into the existing report_compose / report_compile tools.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CATEGORY_LABELS = {
    "food": "Yemek",
    "transport": "Ulaşım",
    "entertainment": "Eğlence",
    "bills": "Faturalar",
    "salary": "Maaş",
    "transfer": "Transfer",
    "atm": "ATM",
    "shopping": "Alışveriş",
    "health": "Sağlık",
    "education": "Eğitim",
    "other": "Diğer",
}


def format_summary(summary: dict, currency: str = "TRY") -> str:
    """Format a finance summary dict as a rich text block."""
    year = summary.get("year", "?")
    month = summary.get("month", "?")
    income = summary.get("income", 0.0)
    expenses = summary.get("expenses", 0.0)
    net = summary.get("net", 0.0)
    by_cat = summary.get("by_category", {})

    month_name = {
        1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
        7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
    }.get(int(month), str(month))

    lines = [
        f"💰 {month_name} {year} — Finansal Özet",
        f"  Gelir:   +{income:>10,.2f} {currency}",
        f"  Gider:    {expenses:>10,.2f} {currency}",
        f"  Net:      {net:>10,.2f} {currency}",
        "",
        "  Kategori Dökümü:",
    ]
    # Sort by absolute value descending
    for cat, total in sorted(by_cat.items(), key=lambda x: abs(x[1]), reverse=True):
        label = CATEGORY_LABELS.get(cat, cat.title())
        sign = "+" if total > 0 else ""
        lines.append(f"    {label:<15} {sign}{total:>10,.2f} {currency}")

    return "\n".join(lines)


def format_budget_status(statuses: list[dict], currency: str = "TRY") -> str:
    """Format budget status list as text with visual bars."""
    if not statuses:
        return "Tanımlı bütçe yok. `finance('set_budget', ...)` ile ekle."
    lines = ["📊 Bütçe Durumu:"]
    for s in statuses:
        cat = CATEGORY_LABELS.get(s["category"], s["category"].title())
        pct = s["pct"]
        limit = s["limit"]
        spent = s["spent"]
        bar_filled = int(pct * 10)
        bar = "█" * bar_filled + "░" * (10 - bar_filled)
        alert = " ⚠" if s["over_threshold"] else ""
        lines.append(
            f"  {cat:<15} [{bar}] {pct:.0%}  "
            f"({spent:,.0f}/{limit:,.0f} {currency}){alert}"
        )
    return "\n".join(lines)


def generate_chart_html(summary: dict, output_dir: Path) -> str | None:
    """Generate a Plotly HTML bar chart; returns path or None if plotly not installed."""
    try:
        import plotly.graph_objects as go
    except ImportError:
        return None

    by_cat = summary.get("by_category", {})
    if not by_cat:
        return None

    categories = [CATEGORY_LABELS.get(c, c.title()) for c in by_cat]
    amounts = list(by_cat.values())
    colors = ["#22c55e" if a > 0 else "#ef4444" for a in amounts]

    fig = go.Figure(
        data=[go.Bar(x=categories, y=amounts, marker_color=colors)],
        layout=go.Layout(
            title=f"{summary.get('year')}-{summary.get('month'):02d} Gelir/Gider",
            xaxis_title="Kategori",
            yaxis_title="TRY",
            paper_bgcolor="#1a1a2e",
            plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"),
        ),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"finance_{summary.get('year')}_{summary.get('month'):02d}.html"
    fig.write_html(str(out_path))
    return str(out_path)
