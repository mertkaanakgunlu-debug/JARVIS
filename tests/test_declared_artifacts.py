"""Post-MVP Faz 1 -- the artifact declaration channel end to end.

jarvis/execution/artifacts.py rests on two concurrency properties that are
easy to state and easy to get wrong, so they are tested rather than trusted:
asyncio.gather gives each tool call its own context copy, and a sync tool
dispatched into a worker thread still appends into the SAME list object the
awaiting parent holds. If either were false the channel would silently
mis-attribute artifacts between concurrent calls -- a verification layer
reporting the wrong file is worse than none.
"""
from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from jarvis.execution import artifacts
from jarvis.graph import safe_tools


# ── the sink itself ────────────────────────────────────────────────────────

def test_declare_outside_a_call_is_a_noop_not_an_error():
    # The same helpers are called by the CLI, scripts/ and unit tests that
    # never go through a ToolNode. Declaring must never be able to break the
    # thing it observes.
    artifacts.declare("C:/somewhere/file.png", kind="chart")
    assert artifacts.declared() == ()


def test_collecting_captures_and_then_restores():
    with artifacts.collecting() as sink:
        artifacts.declare("a.png", kind="chart", produced_by="plot_data")
        artifacts.declare("b.xlsx", kind="workbook")
        assert [a.path for a in sink] == ["a.png", "b.xlsx"]
        assert sink[0].kind == "chart"
    assert artifacts.declared() == ()


def test_nested_collecting_isolates_inner_from_outer():
    with artifacts.collecting() as outer:
        artifacts.declare("outer.png", kind="chart")
        with artifacts.collecting() as inner:
            artifacts.declare("inner.png", kind="chart")
        assert [a.path for a in inner] == ["inner.png"]
        assert [a.path for a in outer] == ["outer.png"]


def test_blank_paths_are_dropped():
    with artifacts.collecting() as sink:
        artifacts.declare("", kind="chart")
        artifacts.declare("   ", kind="chart")
        assert sink == []


async def test_parallel_tasks_do_not_see_each_others_declarations():
    """asyncio.gather copies the context per task -- the property ToolNode's
    own parallel tool dispatch relies on."""
    async def one(name: str) -> list[str]:
        with artifacts.collecting() as sink:
            await asyncio.sleep(0)  # force interleaving
            artifacts.declare(f"{name}.png", kind="chart")
            await asyncio.sleep(0)
            return [a.path for a in sink]

    results = await asyncio.gather(one("a"), one("b"), one("c"))
    assert results == [["a.png"], ["b.png"], ["c.png"]]


async def test_declaration_from_a_worker_thread_reaches_the_parent():
    """copy_context() shares the list OBJECT, so an append made on the thread
    LangChain dispatches sync tools onto is visible to the awaiting caller."""
    def sync_tool_body():
        artifacts.declare("from-thread.png", kind="chart")

    with artifacts.collecting() as sink:
        await asyncio.to_thread(sync_tool_body)
        assert [a.path for a in sink] == ["from-thread.png"]


# ── parse_refs: tolerant, never raises, never upgrades junk ────────────────

@pytest.mark.parametrize("raw", [None, "", 42, {"path": "a.png"}, object()])
def test_parse_refs_rejects_non_sequences(raw):
    assert artifacts.parse_refs(raw) == ()


def test_parse_refs_drops_unusable_entries_but_keeps_good_ones():
    parsed = artifacts.parse_refs([
        {"path": "ok.png", "kind": "chart"},
        "not-a-dict",
        {"kind": "chart"},              # no path -> invalid
        {"path": "x.png", "kind": "no-such-kind"},  # outside the closed vocabulary
        artifacts.ArtifactRef(path="already.xlsx", kind="workbook"),
    ])
    assert [a.path for a in parsed] == ["ok.png", "already.xlsx"]


def test_parse_refs_round_trips_a_model_dump():
    with artifacts.collecting() as sink:
        artifacts.declare("r.tex", kind="report_source", produced_by="report_write")
    payload = [a.model_dump() for a in sink]  # what rides on ToolMessage.artifact
    assert artifacts.parse_refs(payload) == tuple(sink)


# ── safe_tools attaches the declarations to the outgoing ToolMessage ───────

class _Request:
    def __init__(self, name="plot_data", call_id="c1"):
        self.tool_call = {"name": name, "id": call_id}


def _tool_message(content="ok", call_id="c1", artifact=None):
    return ToolMessage(content=content, tool_call_id=call_id, artifact=artifact)


def test_sync_wrapper_attaches_declared_artifacts():
    def execute(_request):
        artifacts.declare("C:/out/chart.png", kind="chart", produced_by="plot_data")
        return _tool_message()

    result = safe_tools._wrap_tool_call(_Request(), execute)
    assert result.artifact == [
        {"path": "C:/out/chart.png", "kind": "chart", "produced_by": "plot_data"}
    ]


async def test_async_wrapper_attaches_declared_artifacts():
    async def execute(_request):
        artifacts.declare("C:/out/book.xlsx", kind="workbook", produced_by="finance.export")
        return _tool_message()

    result = await safe_tools._awrap_tool_call(_Request(), execute)
    assert [a["path"] for a in result.artifact] == ["C:/out/book.xlsx"]


async def test_no_declaration_leaves_artifact_untouched():
    async def execute(_request):
        return _tool_message()

    result = await safe_tools._awrap_tool_call(_Request(), execute)
    assert result.artifact is None


async def test_an_existing_artifact_payload_is_never_overwritten():
    """A tool using LangChain's own response_format="content_and_artifact"
    owns that field; silently clobbering it would break it."""
    async def execute(_request):
        artifacts.declare("mine.png", kind="chart")
        return _tool_message(artifact={"theirs": True})

    result = await safe_tools._awrap_tool_call(_Request(), execute)
    assert result.artifact == {"theirs": True}


async def test_a_failing_tool_keeps_what_it_declared_before_dying():
    """A tool that wrote its file and THEN raised really did produce it."""
    async def execute(_request):
        artifacts.declare("half.png", kind="chart")
        raise RuntimeError("boom after the write")

    result = await safe_tools._awrap_tool_call(_Request(), execute)
    assert result.status == "error"
    assert [a["path"] for a in result.artifact] == ["half.png"]


async def test_a_non_toolmessage_result_passes_through_untouched():
    sentinel = AIMessage(content="a Command-shaped result, not a ToolMessage")

    async def execute(_request):
        artifacts.declare("x.png", kind="chart")
        return sentinel

    assert await safe_tools._awrap_tool_call(_Request(), execute) is sentinel


async def test_the_sink_does_not_leak_between_consecutive_calls():
    async def execute_a(_request):
        artifacts.declare("a.png", kind="chart")
        return _tool_message(call_id="c1")

    async def execute_b(_request):
        return _tool_message(call_id="c2")

    first = await safe_tools._awrap_tool_call(_Request(call_id="c1"), execute_a)
    second = await safe_tools._awrap_tool_call(_Request(call_id="c2"), execute_b)
    assert [a["path"] for a in first.artifact] == ["a.png"]
    assert second.artifact is None


# ── the five real producers declare their REAL path ────────────────────────

def test_latex_write_declares_the_title_derived_path(tmp_path):
    from jarvis.tools.latex import latex_write

    with artifacts.collecting() as sink:
        written = latex_write("Deneme Raporu", "Merhaba", tmp_path)
    # The path is derived from `title`, so it is not discoverable from the
    # call's arguments -- the entire reason this channel exists.
    assert [a.path for a in sink] == [written]
    assert sink[0].kind == "report_source"
    assert (tmp_path / "Deneme_Raporu.tex").is_file()


def test_compose_report_declares_its_path(tmp_path):
    from jarvis.tools.latex import compose_report

    with artifacts.collecting() as sink:
        written = compose_report("Rapor", "## Bolum\nmetin", "[]", tmp_path)
    assert [a.path for a in sink] == [written]
    assert sink[0].produced_by == "report_compose"


def test_plot_data_declares_the_collision_resolved_png(tmp_path):
    """`output` is a filename STEM. generate_plot appends _1/_2 on collision,
    so the declared path must be the one actually written, not the stem."""
    pytest.importorskip("matplotlib")
    pytest.importorskip("pandas")
    import pandas as pd

    from jarvis.tools.plotting import generate_plot

    frame = pd.DataFrame({"x": [1, 2, 3], "y": [1, 4, 9]})
    with artifacts.collecting() as first:
        path_a = generate_plot(None, "line", "x", "y", "t", "", "chart", tmp_path, df=frame)
    with artifacts.collecting() as second:
        path_b = generate_plot(None, "line", "x", "y", "t", "", "chart", tmp_path, df=frame)

    assert path_a != path_b, "second call should not overwrite the first"
    assert [a.path for a in first] == [path_a]
    assert [a.path for a in second] == [path_b]
    assert first[0].kind == "chart"
    for p in (path_a, path_b):
        assert p.endswith(".png")


def test_a_failed_plot_declares_nothing(tmp_path):
    pytest.importorskip("pandas")
    from jarvis.tools.plotting import generate_plot

    with artifacts.collecting() as sink:
        result = generate_plot(None, "line", "x", "y", "t", "", "", tmp_path, df=None)
    assert result.startswith("[ERROR]")
    assert sink == []
