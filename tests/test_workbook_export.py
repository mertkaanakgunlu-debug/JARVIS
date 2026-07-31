"""finance('export') — the cash-flow analysis workbook.

Verified by opening the real file with openpyxl, never by trusting the tool's own
return string. "A file exists" was the oracle that let a degenerate chart pass in
an earlier phase of this project; content is what matters.

Idempotency is asserted SEMANTICALLY, not byte-for-byte. An .xlsx is a zip
containing timestamps, so two runs producing an identical workbook still differ
in bytes; a byte-equality test would fail for a reason that has nothing to do
with correctness. What must hold is: same path, no extra sibling file, and the
same sheets/rows/values/types.
"""
from __future__ import annotations

import json

import pytest

from jarvis.finance_store import FinanceStore
from jarvis.tools.workbook import (
    CHART_SHEET,
    CHART_X,
    CHART_Y,
    SHEET_CATEGORIES,
    SHEET_DAILY,
    SHEET_SUMMARY,
    SHEET_TRANSACTIONS,
    default_export_path,
    export_cashflow_workbook,
    sanitize_cell,
)

openpyxl = pytest.importorskip("openpyxl")


@pytest.fixture
def store(tmp_path):
    s = FinanceStore(tmp_path / "db" / "sessions.db")
    yield s
    s.close()


@pytest.fixture
def workspace(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return ws


def _add(store, uid, date, amount, currency="TRY", category="other", merchant="M",
         description="d"):
    store.upsert_transaction(
        email_uid=uid, bank="burgan", date=date, amount=amount, currency=currency,
        merchant=merchant, category=category, description=description,
    )


def _seed(store):
    """Mirrors the fixture corpus's in-period TRY rows plus the USD outlier."""
    _add(store, "a", "2026-07-07T14:32:00", -250.75, category="food", merchant="MIGROS")
    _add(store, "b", "2026-07-08T09:05:00", -1850.00, category="transport", merchant="SHELL")
    _add(store, "c", "2026-07-10T03:00:00", 42500.00, category="salary", merchant="MAAS")
    _add(store, "d", "2026-07-10T16:20:00", -3000.00, category="transfer", merchant="AHMET")
    _add(store, "e", "2026-07-11T19:44:00", -2000.00, category="atm", merchant="ATM")
    _add(store, "f", "2026-07-13T11:15:00", 4299.90, category="shopping", merchant="TEKNOSA")
    _add(store, "usd", "2026-07-14T10:00:00", -120.50, currency="USD",
         category="shopping", merchant="AMAZON EU")


def _load(path):
    return openpyxl.load_workbook(path, data_only=True)


# ── structure ─────────────────────────────────────────────────────────────────

def test_export_writes_all_four_sheets(store, workspace):
    _seed(store)
    result = export_cashflow_workbook(store, workspace, year=2026, month=7)

    assert not result.startswith("[ERROR]"), result
    wb = _load(default_export_path(workspace, 2026, 7))
    assert wb.sheetnames == [
        SHEET_DAILY, SHEET_TRANSACTIONS, SHEET_SUMMARY, SHEET_CATEGORIES
    ]
    wb.close()


def test_the_chartable_sheet_is_first(store, workspace):
    """Order is load-bearing, not cosmetic. plot_data with no `sheet=` reads
    pandas' default -- the FIRST sheet. With the ledger first, a model that
    forgot `sheet=` hit "Column 'date' not found" and went off renaming columns
    instead of naming a sheet (measured live, qwen3:8b, 2026-07-30). Putting the
    chartable sheet first makes the forgetful path land on usable data."""
    _seed(store)
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    assert wb.sheetnames[0] == CHART_SHEET
    headers = [c.value for c in next(wb[wb.sheetnames[0]].iter_rows(max_row=1))]
    assert CHART_X in headers and CHART_Y in headers
    wb.close()


def test_export_also_produces_the_chart(store, workspace, monkeypatch):
    """The third dependent tool call is the one qwen3:8b could not land: across
    live runs it invented English column names, chose the ledger sheet, passed the
    literal placeholder 'path_to_file', and once emitted the call as a JSON block
    in its reply instead of invoking it. Every figure is already computed here, so
    the tool charts it rather than asking the model to copy data around."""
    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")  # enables the verification sidecar
    _seed(store)
    result = export_cashflow_workbook(store, workspace, year=2026, month=7)

    png = workspace / "exports" / "cashflow_2026-07.png"
    assert png.exists(), result
    assert "cashflow_2026-07.png" in result

    meta = png.parent / f"{png.name}.meta.json"
    assert meta.exists(), "no sidecar -- chart content would be unverifiable"
    recorded = json.loads(meta.read_text(encoding="utf-8"))
    # A full temporal axis: every day of the month, quiet days as real zeros.
    assert len(recorded["y"]) == 31
    assert recorded["x"] != recorded["y"], "not a degenerate x==y line"
    # The plotted values must still be the real daily nets, in the right slots.
    by_day = dict(zip(recorded["x"], recorded["y"]))
    assert by_day["2026-07-07"] == pytest.approx(-250.75)
    assert by_day["2026-07-10"] == pytest.approx(39500.00)
    assert by_day["2026-07-13"] == pytest.approx(4299.90)
    assert by_day["2026-07-05"] == pytest.approx(0.0)


@pytest.mark.parametrize("kind", ["line", "scatter", "bar"])
def test_export_can_produce_the_chart_style_the_user_asked_for(
    store, workspace, monkeypatch, kind
):
    """chart_kind exists because the follow-up path did not work. Asked "grafiği
    bar yerine çizgi grafik yap" (2026-07-30) the model reached for plot_data with
    invented column names and no sheet, failed, asked the user a question, and on
    the next turn fabricated a finished chart file. The reliable path has to be
    able to answer the request itself."""
    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")
    _seed(store)

    export_cashflow_workbook(store, workspace, year=2026, month=7, chart_kind=kind)

    meta = workspace / "exports" / "cashflow_2026-07.png.meta.json"
    recorded = json.loads(meta.read_text(encoding="utf-8"))
    assert recorded["chart_type"] == kind


def test_an_unknown_chart_kind_falls_back_instead_of_failing(store, workspace, monkeypatch):
    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")
    _seed(store)

    result = export_cashflow_workbook(
        store, workspace, year=2026, month=7, chart_kind="pasta"
    )

    assert not result.startswith("[ERROR]")
    meta = workspace / "exports" / "cashflow_2026-07.png.meta.json"
    assert json.loads(meta.read_text(encoding="utf-8"))["chart_type"] == "bar"


def test_the_figures_the_model_must_relay_are_stated_up_front(store, workspace):
    """A live run ended with a reply containing no figures at all -- just a
    question -- while every number sat in this result. The model relays what it
    notices, so the totals lead."""
    _seed(store)
    result = export_cashflow_workbook(store, workspace, year=2026, month=7)

    head = "\n".join(result.splitlines()[:2])
    assert "46,799.90" in head
    assert "-7,100.75" in head
    assert "39,699.15" in head


def test_re_export_does_not_accumulate_chart_files(store, workspace, monkeypatch):
    """generate_plot appends _1/_2 rather than overwriting -- right for
    model-named charts, wrong here: finance is registered idempotency="natural",
    so a second export must converge on ONE png."""
    monkeypatch.setenv("JARVIS_TOOL_TRACE", "1")
    _seed(store)
    export_cashflow_workbook(store, workspace, year=2026, month=7)
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    pngs = sorted(p.name for p in (workspace / "exports").glob("*.png"))
    assert pngs == ["cashflow_2026-07.png"], pngs


def test_a_chart_failure_does_not_lose_the_workbook(store, workspace, monkeypatch):
    """The workbook is the primary deliverable; a broken matplotlib must degrade
    to an honest note, not discard the export."""
    import jarvis.tools.plotting as plotting

    monkeypatch.setattr(
        plotting, "generate_plot", lambda *a, **k: "[ERROR] simulated plot failure"
    )
    _seed(store)
    result = export_cashflow_workbook(store, workspace, year=2026, month=7)

    assert default_export_path(workspace, 2026, 7).exists()
    assert "Grafik oluşturulamadı" in result
    assert not result.startswith("[ERROR]"), "the export itself still succeeded"


def test_transactions_sheet_lists_every_row_including_foreign_currency(store, workspace):
    """Hiding the USD row would be its own lie -- it belongs in the ledger view
    even though it must stay out of the TRY aggregates."""
    _seed(store)
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    ws = wb[SHEET_TRANSACTIONS]
    assert ws.max_row == 8, "7 transactions + header"
    currencies = {ws.cell(row=r, column=3).value for r in range(2, ws.max_row + 1)}
    assert currencies == {"TRY", "USD"}
    wb.close()


def test_summary_sheet_carries_the_correct_try_only_figures(store, workspace):
    """The exact arithmetic: 42500 + 4299.90 income, -7100.75 expense. The USD
    -120.50 must not appear in any of them."""
    _seed(store)
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    ws = wb[SHEET_SUMMARY]
    values = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value
              for r in range(1, ws.max_row + 1)}
    assert values["Gelir"] == pytest.approx(46799.90)
    assert values["Gider"] == pytest.approx(-7100.75)
    assert values["Net"] == pytest.approx(39699.15)
    wb.close()


def test_summary_sheet_says_when_it_excluded_a_currency(store, workspace):
    """A partial total presented as the whole picture is the failure mode; the
    workbook has to admit what it left out."""
    _seed(store)
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    text = " ".join(
        str(c.value) for row in wb[SHEET_SUMMARY].iter_rows() for c in row if c.value
    )
    assert "USD" in text
    wb.close()


def test_categories_sheet_is_the_full_breakdown(store, workspace):
    """Built from category_breakdown(), not top_categories(n=5): six categories
    exist, and a top-5 view would drop one and all income."""
    _seed(store)
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    ws = wb[SHEET_CATEGORIES]
    cats = {ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)}
    assert cats == {"food", "transport", "salary", "transfer", "atm", "shopping"}
    wb.close()


def test_daily_sheet_is_chartable_with_the_advertised_columns(store, workspace):
    """The next-step hint names sheet/x/y; if those headers are not literally
    present, the hint sends the model into a guaranteed [ERROR]."""
    _seed(store)
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    ws = wb[CHART_SHEET]
    headers = [c.value for c in next(ws.iter_rows(max_row=1))]
    assert CHART_X in headers
    assert CHART_Y in headers

    days = [ws.cell(row=r, column=1).value for r in range(2, ws.max_row + 1)]
    # EVERY day of the month, not just the active ones: with quiet days omitted
    # the x-axis is categorical, so a three-day gap and a one-day gap render the
    # same width and the chart misstates when money moved. The owner spotted the
    # symptom ("30 gün yok, 16 bar görüyorum", 2026-07-30).
    assert len(days) == 31, "July has 31 days and all must appear"
    assert days[0] == "2026-07-01" and days[-1] == "2026-07-31"

    # One row per DAY, not per transaction: 07-10 holds both the salary credit
    # and the EFT debit, and they must aggregate into a single point.
    values = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=4).value
              for r in range(2, ws.max_row + 1)}
    assert values["2026-07-10"] == pytest.approx(39500.00)
    # A quiet day is a real zero, not a missing row.
    assert values["2026-07-05"] == pytest.approx(0.0)
    # 07-14 is the USD purchase -- present as a DAY but contributing nothing,
    # because this sheet is TRY-only.
    assert values["2026-07-14"] == pytest.approx(0.0)

    tenth = next(r for r in range(2, ws.max_row + 1)
                 if ws.cell(row=r, column=1).value == "2026-07-10")
    assert ws.cell(row=tenth, column=2).value == pytest.approx(42500.00), "income"
    assert ws.cell(row=tenth, column=3).value == pytest.approx(-3000.00), "expense"
    assert ws.cell(row=tenth, column=4).value == pytest.approx(39500.00), "net"
    wb.close()


def test_result_paths_are_workspace_relative(store, workspace):
    """An absolute host path leaks the machine layout into model context and into
    the user-visible reply -- tests/test_no_host_path_leak.py guards the same
    class elsewhere. It also invites the model to echo a Windows path back as a
    tool argument, which a live run did."""
    _seed(store)
    result = export_cashflow_workbook(store, workspace, year=2026, month=7)

    assert "exports/cashflow_2026-07.xlsx" in result
    assert "exports/cashflow_2026-07.png" in result
    assert "C:\\" not in result and str(workspace) not in result


# ── idempotency (semantic, not byte-for-byte) ──────────────────────────────────

def _snapshot(path):
    wb = _load(path)
    snap = {
        name: [[(c.value, type(c.value).__name__) for c in row]
               for row in wb[name].iter_rows()]
        for name in wb.sheetnames
    }
    wb.close()
    return snap


def test_re_export_overwrites_in_place_and_creates_no_sibling(store, workspace):
    """finance is registered idempotency="natural" (a converging write). Minting a
    fresh RunContext directory per call -- plot_data's pattern -- would break that
    guarantee, so this asserts exactly one artifact survives two runs."""
    _seed(store)
    export_cashflow_workbook(store, workspace, year=2026, month=7)
    first = _snapshot(default_export_path(workspace, 2026, 7))

    export_cashflow_workbook(store, workspace, year=2026, month=7)
    second = _snapshot(default_export_path(workspace, 2026, 7))

    assert list((workspace / "exports").glob("*.xlsx")) == [
        default_export_path(workspace, 2026, 7)
    ]
    assert first == second, "same inputs must yield the same workbook content"


def test_export_reflects_new_data_on_re_run(store, workspace):
    """Idempotent must not mean frozen -- a converging write converges on the
    CURRENT state."""
    _seed(store)
    export_cashflow_workbook(store, workspace, year=2026, month=7)
    _add(store, "new", "2026-07-20T10:00:00", -500.0, category="food")
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    assert wb[SHEET_TRANSACTIONS].max_row == 9
    wb.close()


# ── formula injection ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("payload", [
    '=HYPERLINK("http://evil.example/x","Click")',
    "=cmd|'/c calc'!A1",
    "+1+1",
    "-1+1",
    "@SUM(A1:A9)",
])
def test_sanitize_neutralizes_formula_leaders(payload):
    assert sanitize_cell(payload).startswith("'")


def test_mail_derived_merchant_cannot_smuggle_a_formula(store, workspace):
    """Merchant text comes from email anyone can send. Excel executes a leading
    '=' when the owner opens the file, so this is a real injection path -- and
    nothing in this repo sanitized for it before."""
    _add(store, "evil", "2026-07-07T14:00:00", -10.0,
         merchant='=cmd|\'/c calc\'!A1', description='=HYPERLINK("http://evil","x")')
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    ws = wb[SHEET_TRANSACTIONS]
    merchant = ws.cell(row=2, column=5).value
    description = ws.cell(row=2, column=7).value
    assert not str(merchant).startswith("=")
    assert not str(description).startswith("=")
    wb.close()


def test_numbers_are_not_quoted_into_strings(store, workspace):
    """Sanitizing must not touch computed values: quoting them would turn every
    amount into text and break plot_data downstream."""
    _seed(store)
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    assert isinstance(wb[SHEET_TRANSACTIONS].cell(row=2, column=2).value, (int, float))
    wb.close()


# ── limits, paths, failure modes ──────────────────────────────────────────────

def test_more_than_200_transactions_are_not_truncated(store, workspace):
    """list_transactions() defaults to limit=200; exporting through it would have
    silently dropped everything past row 200."""
    for i in range(250):
        _add(store, f"u{i}", f"2026-07-{(i % 28) + 1:02d}T10:00:{i % 60:02d}", -10.0)
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    assert wb[SHEET_TRANSACTIONS].max_row == 251
    wb.close()


def test_explicit_output_is_confined_to_the_workspace(store, workspace):
    _seed(store)
    result = export_cashflow_workbook(
        store, workspace, year=2026, month=7, output="../escape.xlsx"
    )
    assert result.startswith("[ERROR]"), result


def test_output_under_data_is_refused(store, workspace):
    """`data` is in files.PROTECTED_DIRS, so _resolve() rejects it -- which is
    exactly why the default export path is exports/, not data/finance/."""
    _seed(store)
    result = export_cashflow_workbook(
        store, workspace, year=2026, month=7, output="data/finance/cashflow.xlsx"
    )
    assert result.startswith("[ERROR]"), result


def test_a_filename_naming_the_wrong_period_is_refused_not_renamed(store, workspace):
    """Two lessons in one test.

    A live qwen3:8b run asked for cashflow_2026-05.xlsx while exporting July, so
    the file on disk contradicted the workbook, the summary and the reply.

    The first fix silently renamed it -- and made things worse: the model kept
    using the path IT had asked for, its own next call got "file not found", and
    it told the user the export had failed. 2 of 5 runs reported failure for a
    workbook that had been written correctly. Rejecting instead keeps the caller's
    model of the world intact, the same reason args_schemas validation rejects
    rather than substituting a corrected form."""
    _seed(store)
    result = export_cashflow_workbook(
        store, workspace, year=2026, month=7, output="exports/cashflow_2026-05.xlsx"
    )

    assert result.startswith("[ERROR]"), result
    assert "2026-07" in result, "the error must name the period that IS correct"
    assert not (workspace / "exports" / "cashflow_2026-05.xlsx").exists()
    assert not (workspace / "exports" / "cashflow_2026-07.xlsx").exists()


def test_the_model_cannot_choose_the_filename_at_all():
    """Belt and braces on the above: the tool the model sees has no `output`
    parameter, and FinanceArgs (extra="forbid") has no such field either, so the
    mismatch is unreachable from a model call rather than merely handled."""
    import inspect

    from jarvis.execution.args_schemas import FinanceArgs

    assert "output" not in FinanceArgs.model_fields

    with pytest.raises(Exception):
        FinanceArgs(action="export", output="exports/whatever.xlsx")

    # And the wrapper's own signature, so the schema and the tool cannot drift.
    src = inspect.getsource(
        __import__("jarvis.graph.tools", fromlist=["make_tools"]).make_tools
    )
    finance_def = src.split("def finance(")[1].split(") -> str:")[0]
    assert "output" not in finance_def, finance_def


def test_a_filename_with_no_period_is_left_alone(store, workspace):
    """Only a CONTRADICTING period is corrected -- an arbitrary name the owner
    chose deliberately must survive."""
    _seed(store)
    export_cashflow_workbook(
        store, workspace, year=2026, month=7, output="reports/benim_raporum.xlsx"
    )

    assert (workspace / "reports" / "benim_raporum.xlsx").exists()


def test_explicit_output_gets_an_xlsx_suffix(store, workspace):
    _seed(store)
    export_cashflow_workbook(
        store, workspace, year=2026, month=7, output="reports/temmuz"
    )
    assert (workspace / "reports" / "temmuz.xlsx").exists()


def test_empty_database_error_tells_the_model_to_act_not_to_ask(store, workspace):
    """An empty workbook would let the chain "succeed" with nothing in it.

    The wording is load-bearing. Measured 2026-07-30: the model called export
    before sync in 5/5 runs of one batch, read the old passive "Önce
    finance('sync') ile mailleri tara" as advice to pass on, printed it and
    stopped -- with three tool rounds still unused. Both errors this project has
    successfully self-corrected are phrased as a specific next call."""
    result = export_cashflow_workbook(store, workspace, year=2026, month=7)

    assert result.startswith("[ERROR]")
    assert "ŞİMDİ" in result, "must name a specific next call, not offer advice"
    assert "Kullanıcıya soru sorma" in result
    assert not default_export_path(workspace, 2026, 7).exists()


def test_empty_database_error_names_both_data_routes():
    """It must not presume the cause. An earlier version said only "call sync", so
    when the user pointed at a PDF statement ("indirilenlerdeki Hesap
    Hareketleri.pdf") the model was pushed down the mail path and never retried
    import_statement with a workable path."""
    import tempfile
    from pathlib import Path

    from jarvis.finance_store import FinanceStore

    with tempfile.TemporaryDirectory() as tmp:
        s = FinanceStore(Path(tmp) / "db" / "sessions.db")
        try:
            result = export_cashflow_workbook(s, Path(tmp), year=2026, month=7)
        finally:
            s.close()

    assert "import_statement" in result, "the statement route must be offered"
    assert "sync" in result, "the mail route must be offered"


def test_empty_period_names_the_periods_that_do_have_data(store, workspace):
    """The error that cost 5/5 gate runs. Asked for month=5 with July populated,
    the old message said only "run finance('sync') first" -- so the model looped on
    sync and reported the export as broken while July's ledger was complete. An
    error must name the correct retry, not misdiagnose the cause."""
    _seed(store)  # July data only

    result = export_cashflow_workbook(store, workspace, year=2026, month=5)

    assert result.startswith("[ERROR]")
    assert "2026-07" in result, "must name the period that HAS data"
    assert "month=7" in result, "must spell out the corrected call"
    assert "Tekrar sync gerekmiyor" in result, "must not send the caller back to sync"


def test_other_months_are_excluded(store, workspace):
    _seed(store)
    _add(store, "jun", "2026-06-22T13:00:00", -310.00, category="food")
    export_cashflow_workbook(store, workspace, year=2026, month=7)

    wb = _load(default_export_path(workspace, 2026, 7))
    assert wb[SHEET_TRANSACTIONS].max_row == 8, "the June row must not appear"
    wb.close()
