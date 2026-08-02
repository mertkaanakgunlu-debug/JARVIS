"""The chart tools as the model actually reaches them — GPT review, Faz 5 gate.

`test_working_set_integrity.py` calls the chart functions directly. These go
through the real `@tool` objects `make_tools()` returns, because two of the
review's findings live in the wiring rather than the logic:

  * a chart drawn from inline `data_json` was registered with `source=""`,
    which resolves to the workspace DIRECTORY. It appeared in the working set
    as editable and failed on the first revision, because the data that drew it
    existed only in a local DataFrame. "Grafik çizildi ama sadece bazı
    grafikler revize edilebiliyor" is an invisible distinction to a user.
  * the tools called `default_store()` per invocation, which -- despite the
    name -- built a new store and a new SQLite connection every time and never
    closed it, so the agent's long-lived store and the tools' store were
    different objects over the same file.
"""
from __future__ import annotations

from pathlib import Path

from jarvis.config import Settings
from jarvis.graph import tools as graph_tools
from jarvis.memory import Memory
from jarvis.working_set import KIND_CHART, WorkingSetStore

CONVERSATION = "c-wiring"
CONFIG = {"configurable": {"conversation_id": CONVERSATION}}


def _tools(tmp_path, store):
    settings = Settings()
    memory = Memory(settings)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    built = graph_tools.make_tools(workspace, settings, memory, store)
    return {t.name: t for t in built}, workspace


def _store(tmp_path) -> WorkingSetStore:
    return WorkingSetStore(tmp_path / "ws.db")


# ── inline data: drawn means revisable ───────────────────────────────────────

def test_an_inline_chart_can_be_revised(isolated_cwd, tmp_path):
    """THE regression: plot_data(data_json=...) → chart_revise(color=...)
    must produce a new PNG for the SAME object."""
    store = _store(tmp_path)
    try:
        tools, _ = _tools(tmp_path, store)

        first = tools["plot_data"].invoke(
            {"kind": "line", "data_json": '{"x": [1,2,3], "y": [1,4,9]}',
             "x": "x", "y": "y", "title": "Inline"},
            config=CONFIG,
        )
        assert not first.startswith("[ERROR]"), first

        obj = store.active(CONVERSATION, KIND_CHART)
        assert obj is not None, "an inline chart must still enter the working set"

        revised = tools["chart_revise"].invoke({"color": "kırmızı"}, config=CONFIG)

        assert not revised.startswith("[ERROR]"), revised
        after = store.active(CONVERSATION, KIND_CHART)
        assert after.id == obj.id, "revision must patch the same object, not fork one"
        assert after.spec["color"] == "kırmızı"
    finally:
        store.close()


def test_inline_data_is_materialized_next_to_its_chart(isolated_cwd, tmp_path):
    """The mechanism behind the test above: the numbers reach disk, so a later
    turn re-reads exactly what drew the chart."""
    store = _store(tmp_path)
    try:
        tools, _ = _tools(tmp_path, store)
        tools["plot_data"].invoke(
            {"kind": "line", "data_json": "[1,4,9,16]"},
            config=CONFIG,
        )

        spec = store.active(CONVERSATION, KIND_CHART).spec
        source = Path(spec["source"])
        assert source.exists(), f"inline source not persisted: {source}"
        assert spec.get("_source_type") == "inline_materialized"
    finally:
        store.close()


def test_a_file_backed_chart_is_unchanged(isolated_cwd, tmp_path):
    """The materialization must not touch the ordinary path."""
    store = _store(tmp_path)
    try:
        tools, workspace = _tools(tmp_path, store)
        (workspace / "satis.csv").write_text("ay,satis\n1,120\n2,145\n", encoding="utf-8")

        out = tools["plot_data"].invoke(
            {"path": "satis.csv", "x": "ay", "y": "satis"},
            config=CONFIG,
        )
        assert not out.startswith("[ERROR]"), out

        spec = store.active(CONVERSATION, KIND_CHART).spec
        assert spec["source"] == "satis.csv"
        assert "_source_type" not in spec
    finally:
        store.close()


# ── one store, not one per call ──────────────────────────────────────────────

def test_the_tools_write_to_the_injected_store(isolated_cwd, tmp_path):
    """If the tools built their own store, this object would land in a
    different database and the agent would never see it."""
    store = _store(tmp_path)
    try:
        tools, _ = _tools(tmp_path, store)
        tools["plot_data"].invoke(
            {"kind": "line", "data_json": "[1,4,9]"},
            config=CONFIG,
        )
        assert store.list(CONVERSATION), "the injected store received nothing"
    finally:
        store.close()


def test_default_store_is_actually_one_store(isolated_cwd, tmp_path):
    """It was a constructor wearing a singleton's name."""
    from jarvis.working_set import default_store, reset_default_store

    reset_default_store()
    try:
        assert default_store() is default_store()
    finally:
        reset_default_store()


def test_no_conversation_id_registers_nothing(isolated_cwd, tmp_path):
    """A workflow step or a background render is nobody's conversation --
    inventing an owner would put a chart into a transcript that never
    mentioned one. Unchanged by the materialization work."""
    store = _store(tmp_path)
    try:
        tools, _ = _tools(tmp_path, store)
        out = tools["plot_data"].invoke({"kind": "line", "data_json": "[1,4,9]"})
        assert not out.startswith("[ERROR]"), out
        assert store.list("") == []
    finally:
        store.close()
