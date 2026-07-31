"""Cash-flow analysis workbook (.xlsx) — the MVP's "excel tablosuna dönüştür".

Design decisions worth knowing before touching this:

**The tool does the analysis, not the model.** Every figure here is computed by
FinanceStore from the ledger. The alternative -- letting the model serialize
transaction rows into a tool argument -- puts a local 8B model in charge of
copying numbers, which is precisely where this project's known hallucination
failures live (``plot_data`` sometimes printing Python instead of calling the
tool; a chart plotting values against themselves). The model chooses the period.
It never carries the data.

**Deterministic path, overwritten in place.** ``exports/cashflow_<period>.xlsx``
under the workspace. Not ``RunContext.for_execution()``: that mints a fresh
``exec-<timestamp>-<uuid>`` directory per call, which would mean re-running the
same export produced a different file every time -- contradicting ``finance``'s
registered ``idempotency="natural"`` (a converging write). It also cannot live
under ``data/``, which ``files._resolve()`` refuses via ``PROTECTED_DIRS``.

**Currency is never mixed.** The transactions sheet lists every row, including
foreign ones, because hiding them would be its own lie. Every *aggregate* sheet
is single-currency and says so in its header.

**Mail-derived text is sanitized.** Merchant and description come from email a
stranger can send. Excel treats a leading ``=``/``+``/``-``/``@`` as a formula,
so an attacker-controlled merchant name is a CSV/Excel-injection vector the
moment the owner opens the file. Nothing in this repo did this before.
"""
from __future__ import annotations

import re
from pathlib import Path

from jarvis.finance_store import DEFAULT_CURRENCY

# openpyxl's own limit; a value beyond it makes the file unopenable.
MAX_CELL_CHARS = 32_000

_PERIOD_IN_NAME = re.compile(r"20\d{2}[-_]\d{2}")

SHEET_TRANSACTIONS = "İşlemler"
SHEET_SUMMARY = "Özet"
SHEET_CATEGORIES = "Kategori"
SHEET_DAILY = "Günlük Akış"

# The sheet a chart should be drawn from, and the columns to use. Exported so the
# next-step hint and the tests agree on one answer instead of two.
CHART_SHEET = SHEET_DAILY
CHART_X = "Gün"
CHART_Y = "Net"

_MONTHS_TR = {
    1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
    7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık",
}

# Excel/LibreOffice read a cell beginning with any of these as a formula.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def sanitize_cell(value):
    """Neutralize spreadsheet formula injection in text taken from email.

    A merchant field reading ``=HYPERLINK("http://evil","Click")`` -- or the
    classic ``=cmd|'/c calc'!A1`` DDE payload -- executes when the owner opens
    the workbook. Prefixing with an apostrophe forces Excel to treat the value as
    literal text; the displayed string is unchanged for the reader.

    Numbers and dates pass through untouched: they are computed here, never
    attacker-supplied, and quoting them would break every downstream chart by
    turning them into strings.
    """
    if not isinstance(value, str):
        return value
    text = value.replace("\x00", "")
    if len(text) > MAX_CELL_CHARS:
        text = text[:MAX_CELL_CHARS]
    if text[:1] in _FORMULA_LEADERS:
        return "'" + text
    return text


def default_export_path(workspace: Path, year: int, month: int) -> Path:
    """``<workspace>/exports/cashflow_<YYYY-MM>.xlsx`` -- stable across runs."""
    return workspace / "exports" / f"cashflow_{year:04d}-{month:02d}.xlsx"


def _rel(path: Path, workspace: Path) -> str:
    """Workspace-relative POSIX path for anything user- or model-visible.

    An absolute Windows path in a tool result leaks the host layout into model
    context and into the reply; tests/test_no_host_path_leak.py guards that class
    of regression elsewhere in this codebase.
    """
    try:
        return path.resolve().relative_to(workspace.resolve()).as_posix()
    except ValueError:
        return path.name


CHART_KINDS = ("bar", "line", "scatter")


def export_cashflow_workbook(
    store,
    workspace: Path,
    *,
    year: int,
    month: int,
    currency: str = DEFAULT_CURRENCY,
    output: str = "",
    chart_kind: str = "bar",
) -> str:
    """Write the analysis workbook and return a human+model readable result.

    Returns a string starting with ``[ERROR]`` on failure, matching this repo's
    tool-result convention (jarvis/graph/tool_accounting.content_is_failure).
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
        from openpyxl.utils import get_column_letter
    except ImportError:
        return "[ERROR] openpyxl is not installed. Run: pip install openpyxl"

    period = f"{year:04d}-{month:02d}"

    if output:
        # User/model-supplied destination: confine it with the same guard
        # file_write uses, rather than inventing a second path policy.
        from jarvis.tools.files import _resolve
        try:
            target = _resolve(output, workspace)
        except PermissionError as exc:
            return f"[ERROR] {exc}"
        if target.suffix.lower() != ".xlsx":
            target = target.with_suffix(".xlsx")
        # A filename must never contradict its contents -- but it is REFUSED, not
        # silently corrected. Both halves of that were learned the hard way:
        #
        # A live qwen3:8b run passed output='exports/cashflow_2026-05.xlsx' for a
        # July export, so the file on disk said May while the workbook, the
        # summary and the reply all said Temmuz. The first fix silently renamed it
        # to July -- and made things worse: the model kept using the path IT had
        # asked for, got "file not found" from its own next call, and concluded
        # the export had failed. Two of five runs then reported failure for a
        # workbook that had been written correctly.
        #
        # This is the same principle jarvis/execution/args_schemas.py already
        # follows for validation: reject, never substitute a corrected form back
        # into a call, because the caller's next step reasons about the arguments
        # it believes it sent.
        stem = target.stem
        found = _PERIOD_IN_NAME.search(stem)
        if found and found.group(0).replace("_", "-") != period:
            return (
                f"[ERROR] Dosya adı '{target.name}' {found.group(0)} dönemini "
                f"gösteriyor ama veri {period} dönemine ait. output parametresini "
                f"kaldır (varsayılan ad zaten doğru: "
                f"{_rel(default_export_path(workspace, year, month), workspace)}) "
                "veya adı düzelt."
            )
    else:
        target = default_export_path(workspace, year, month)

    label = f"{_MONTHS_TR.get(month, month)} {year}"

    transactions = list(store.iter_transactions(year=year, month=month))
    total_rows = store.count_transactions(year=year, month=month)
    summary = store.summary(year=year, month=month, currency=currency)
    categories = store.category_breakdown(year=year, month=month, currency=currency)
    # fill_period: the chart's x-axis must be temporal, not categorical -- see
    # FinanceStore.daily_flow's own docstring for why omitting quiet days
    # misstates when money actually moved.
    daily = store.daily_flow(
        year=year, month=month, currency=currency, fill_period=True
    )
    present = store.currencies_in_period(year=year, month=month)
    others = [c for c in present if c != currency]

    if not transactions:
        # Name the periods that DO have data. The old message said only "önce
        # finance('sync') ile mailleri tara", which is wrong once sync has already
        # succeeded -- and a live qwen3:8b run (5/5) took that advice literally:
        # it asked for month=5, got told to sync, looped on sync, and reported the
        # export as broken while July's ledger sat fully populated. An error that
        # names the correct retry is self-correcting; one that misdiagnoses is a
        # trap. Same reasoning as plot_data naming the other sheets.
        available = store.periods_with_data()
        if available:
            return (
                f"[ERROR] {period} döneminde kayıtlı işlem yok. Veri OLAN dönemler: "
                f"{', '.join(available)}. Bu dönemlerden biriyle tekrar çağır — "
                f"örn. finance('export', year={available[0][:4]}, "
                f"month={int(available[0][5:7])}) — ya da year/month vermeyip "
                "içinde bulunulan aya bırak. Tekrar sync gerekmiyor."
            )
        # Imperative and addressed to the MODEL, not advice to relay: the model
        # called export before sync in 5/5 runs of one batch, read a passive
        # "önce sync yap" as a message for the user, printed it and stopped with
        # three tool rounds unused.
        #
        # It must also name BOTH routes. An earlier version said only "call
        # sync" -- so when the user pointed at a PDF statement ("indirilenlerdeki
        # Hesap Hareketleri.pdf") the model was pushed toward the mail path and
        # never retried import_statement with a better path. An error that
        # presumes the cause sends the caller the wrong way.
        return (
            "[ERROR] Veritabanı boş — henüz hiç işlem kaydedilmemiş. Kaynağa göre "
            "ŞİMDİ şunlardan BİRİNİ çağır, sonra finance('export') çağrısını "
            "tekrarla: kullanıcı bir ekstre/PDF dosyası gösterdiyse "
            "finance('import_statement', path='<dosyanın TAM yolu>'); "
            "banka bildirim maillerinden okunacaksa finance('sync'). "
            "Kullanıcıya soru sorma, çağrıları sen yap."
        )

    wb = Workbook()
    bold = Font(bold=True)

    # ── daily flow FIRST -- deliberately, for two different readers ────────────
    # A human opening a cash-flow workbook wants the flow, not a ledger dump.
    # And plot_data with no `sheet=` reads pandas' default, which is the FIRST
    # sheet: with the transactions sheet first, a model that forgot `sheet=` got
    # "[ERROR] Column 'date' not found" and went off trying to rename columns
    # (measured, 2026-07-30). Ordering the chartable sheet first makes the
    # forgetful path land on usable data instead of a dead end.
    ws = wb.active
    ws.title = SHEET_DAILY
    ws.append([CHART_X, "Gelir", "Gider", CHART_Y, "Kümülatif Net", "İşlem"])
    for cell in ws[1]:
        cell.font = bold
    for d in daily:
        ws.append([
            d["day"], d["income"], d["expense"],
            d["net"], d["running_net"], d["count"],
        ])

    # ── transactions: every row, every currency ───────────────────────────────
    ws = wb.create_sheet(SHEET_TRANSACTIONS)
    tx_headers = ["Tarih", "Tutar", "Para Birimi", "Yön", "Satıcı", "Kategori", "Açıklama"]
    ws.append(tx_headers)
    for cell in ws[1]:
        cell.font = bold
    for t in transactions:
        amount = t.get("amount") or 0.0
        ws.append([
            sanitize_cell(t.get("date", "")),
            amount,
            sanitize_cell(t.get("currency") or ""),
            "Gelir" if amount > 0 else "Gider",
            sanitize_cell(t.get("merchant") or ""),
            sanitize_cell(t.get("category") or ""),
            sanitize_cell(t.get("description") or ""),
        ])

    # ── summary: the headline figures, explicitly single-currency ─────────────
    ws = wb.create_sheet(SHEET_SUMMARY)
    ws.append([f"{label} — Nakit Akışı Özeti ({currency})"])
    ws["A1"].font = bold
    ws.append([])
    ws.append(["Ölçüt", "Değer", "Para Birimi"])
    for cell in ws[3]:
        cell.font = bold
    for name, value in (
        ("Gelir", summary["income"]),
        ("Gider", summary["expenses"]),
        ("Net", summary["net"]),
    ):
        ws.append([name, value, currency])
    ws.append(["İşlem sayısı", summary["count"], ""])
    if others:
        ws.append([])
        ws.append([
            f"Not: bu özet yalnızca {currency} işlemlerini kapsar. "
            f"Ayrıca {', '.join(others)} işlem(ler)i var ve toplamlara katılmadı."
        ])

    # ── categories: FULL breakdown, not a top-N view ──────────────────────────
    ws = wb.create_sheet(SHEET_CATEGORIES)
    ws.append(["Kategori", "Gelir", "Gider", "Net", "İşlem", "Para Birimi"])
    for cell in ws[1]:
        cell.font = bold
    for c in categories:
        ws.append([
            sanitize_cell(c["category"]), c["income"], c["expense"],
            c["net"], c["count"], c["currency"],
        ])

    for sheet in wb.worksheets:
        for idx in range(1, sheet.max_column + 1):
            sheet.column_dimensions[get_column_letter(idx)].width = 18

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        wb.save(target)
    except OSError as exc:
        return f"[ERROR] Workbook kaydedilemedi: {exc}"
    finally:
        wb.close()

    rel = _rel(target, workspace)

    # ── chart the flow here, rather than leaving it to a third tool call ───────
    # Measured on qwen3:8b (2026-07-30): sync -> export succeeded reliably, and
    # the third dependent call did not. Across runs the model called plot_data
    # with invented English column names, with sheet='İşlemler' instead of the
    # chart sheet, with the literal placeholder path 'path_to_file', and once
    # emitted the whole call as a JSON code block in its reply instead of
    # invoking it (the "plot_data prints code instead of calling" pattern
    # HANDOFF.md already records). It could repair one argument per round but not
    # hold the whole set together.
    #
    # So the tool does it. The user asked for a graph, not for a specific tool to
    # be invoked, and every figure is already computed here -- handing the model
    # a path and six column names just to get them read back is asking it to
    # copy data, which is the one thing it is worst at. plot_data stays fully
    # available for charting anything else; this only removes the mandatory
    # third hop from the money path.
    chart_rel = ""
    chart_note = ""
    if daily:
        try:
            from jarvis.run_context import RunContext  # noqa: F401  (kept for parity)
            from jarvis.tools.plotting import generate_plot
            import pandas as pd

            frame = pd.DataFrame([
                {CHART_X: d["day"], CHART_Y: d["net"]} for d in daily
            ])
            png_target = target.with_suffix(".png")
            # generate_plot appends _1/_2 rather than overwriting -- correct for
            # model-named charts, wrong here: a second export must converge on
            # ONE artifact (finance is registered idempotency="natural"), not
            # accumulate cashflow_2026-07_1.png, _2, ...
            for stale in (png_target, png_target.with_suffix(".png.meta.json")):
                try:
                    stale.unlink(missing_ok=True)
                except OSError:
                    pass
            # chart_kind exists so "bar yerine çizgi grafik yap" is served by
            # THIS call rather than a follow-up plot_data hop. Measured
            # 2026-07-30: asked exactly that, the model called plot_data with
            # invented column names and no sheet, failed, asked the user a
            # question, and on the next turn fabricated a finished chart. The
            # reliable path has to be able to answer the request.
            kind = (chart_kind or "bar").strip().lower()
            if kind not in CHART_KINDS:
                kind = "bar"
            produced = generate_plot(
                None, kind, CHART_X, CHART_Y,
                f"{label} — Nakit Akışı ({currency})", "",
                png_target.stem, png_target.parent, df=frame,
            )
            if produced.startswith("[ERROR]"):
                chart_note = f"   ⚠ Grafik oluşturulamadı: {produced}"
            else:
                chart_rel = _rel(Path(produced), workspace)
        except Exception as exc:  # noqa: BLE001 -- a failed chart must not lose the workbook
            chart_note = f"   ⚠ Grafik oluşturulamadı: {type(exc).__name__}: {exc}"

    truncated = ""
    if total_rows != len(transactions):  # pragma: no cover -- iter_transactions is unbounded
        truncated = f" ⚠ {total_rows - len(transactions)} işlem yazılamadı."

    # The next-step instruction goes FIRST, on its own line, in imperative form.
    # It used to be the last of five lines and a live qwen3:8b run read straight
    # past it -- calling plot_data with invented English column names
    # (x='date', y='amount') and no sheet at all. Position and brevity are doing
    # real work here, not decoration.
    # The headline figures come FIRST and spelled out, because the model has to
    # relay them to the user and it will only relay what it noticed. A live run
    # ended with the reply containing no figures at all -- just a question about
    # column names -- while every number was already sitting in this result.
    lines = [
        f"✅ Excel tablosu hazır: {rel}"
        + (f" · Grafik hazır: {chart_rel}" if chart_rel else ""),
        f"KULLANICIYA ŞU RAKAMLARI BİLDİR — {label} ({currency}): "
        f"Gelir {summary['income']:,.2f} · Gider {summary['expenses']:,.2f} · "
        f"Net {summary['net']:,.2f} · {summary['count']} işlem",
        "   Excel ve grafik oluşturuldu; başka bir araç çağırmaya gerek yok.",
        f"   Sayfalar: {', '.join(s.title for s in wb.worksheets)}",
        # Advertise the follow-up explicitly. The parameter alone was not enough:
        # asked "grafiği bar yerine çizgi grafik yap" the model reached for
        # plot_data with invented column names instead of re-running export
        # (measured twice, 2026-07-30). Naming the exact call is the technique
        # that has worked every other time in this tool's results.
        "   Kullanıcı grafik türünü değiştirmek isterse (çizgi/nokta/bar), "
        "plot_data DEĞİL şunu çağır: "
        "finance('export', chart_kind='line'|'scatter'|'bar').",
    ]
    if chart_note:
        lines.append(chart_note)
    if others:
        lines.append(
            f"   ℹ Toplamlar yalnızca {currency}; {', '.join(others)} işlem(ler)i "
            "işlem sayfasında listelendi ama toplamlara katılmadı."
        )
    if truncated:
        lines.append(f"  {truncated}")
    if not chart_rel and not chart_note:
        # No chart and no explanation would be a silent gap; say so, and name the
        # call that would produce one.
        lines.append(
            f"   Grafik için: plot_data(path='{rel}', sheet='{CHART_SHEET}', "
            f"kind='bar', x='{CHART_X}', y='{CHART_Y}')"
        )
    return "\n".join(lines)
