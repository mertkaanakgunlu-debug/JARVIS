"""Working Set spec integrity — the GPT review's Faz 5 entry gate.

Regime A (deterministic). Faz 4 shipped a store whose semantics were right and
a tool layer that reached only part of them. Each test here pins one gap the
review found, and each one is a state the USER can reach in one sentence:

  * "rengi kırmızı yap" on a grouped chart stored `hue` AND `color`. The
    renderer honours `hue` and ignores `color`, so the chart did not change --
    while the prompt told the model, every turn, that it had.
  * "başlığı 'X' yap" wrote the spec but not the title column, so the prompt
    then showed two different titles for one object.
  * "başlığı kaldır" was unaskable: the store has supported deletion since day
    one, and nothing model-facing could express it.
  * `undo` committed before it rendered, so a failed redraw left stored state
    and the on-screen PNG disagreeing.
  * revising an inactive chart left the OTHER one active, so the next
    "şimdi başlığını da değiştir" silently edited a different chart.

The common shape: the store could express the right thing and the layer above
it could not. That is why these are tool-level tests rather than store-level
ones -- `test_working_set.py` already proves the store.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.tools.chart_objects import (
    canonicalize_chart_patch,
    chart_revise,
    register_chart,
    working_set_control,
)
from jarvis.working_set import KIND_CHART, WorkingSetStore

CSV = "ay,satis,gider,bolge\n2026-01,120,80,Ege\n2026-02,145,90,Ege\n2026-03,132,85,İç\n"


@pytest.fixture
def workspace(tmp_path) -> Path:
    (tmp_path / "satis.csv").write_text(CSV, encoding="utf-8")
    return tmp_path


@pytest.fixture
def store(tmp_path) -> WorkingSetStore:
    s = WorkingSetStore(tmp_path / "ws.db")
    yield s
    s.close()


def _base(store, workspace, plots):
    return {
        "conversation_id": "c1", "store": store,
        "workspace": workspace, "plots_dir": plots,
    }


def _chart(store, workspace, **kwargs):
    defaults = {"png": "a.png", "source": "satis.csv", "x": "ay", "y": "satis", "kind": "line"}
    return register_chart(
        conversation_id="c1", store=store, workspace=workspace,
        **{**defaults, **kwargs},
    )


@pytest.fixture
def plots(tmp_path) -> Path:
    d = tmp_path / "plots"
    d.mkdir()
    return d


# ── hue / color: one canonical mutation path ─────────────────────────────────

def test_setting_color_on_a_grouped_chart_drops_the_hue(store, workspace, plots):
    """THE regression. The exclusion lived only on the redraw path; a direct
    `chart_revise` could store both, and then the renderer ignored `color`."""
    obj = _chart(store, workspace, hue="bolge")
    assert obj.spec["hue"] == "bolge"

    chart_revise(**_base(store, workspace, plots), changes={"color": "kırmızı"})

    spec = store.get(obj.id).spec
    assert spec.get("color") == "kırmızı"
    assert "hue" not in spec, "a spec claiming both states something untrue about its own output"


def test_setting_hue_on_a_coloured_chart_drops_the_color(store, workspace, plots):
    obj = _chart(store, workspace, color="kırmızı")
    chart_revise(**_base(store, workspace, plots), changes={"hue": "bolge"})

    spec = store.get(obj.id).spec
    assert spec.get("hue") == "bolge"
    assert "color" not in spec


def test_canonicalize_is_the_shared_rule():
    """Both callers go through one function -- that is the actual fix; the two
    tests above only prove it is wired at each call site."""
    assert canonicalize_chart_patch({"hue": "bolge"}, {"color": "red"}) == {
        "color": "red", "hue": None,
    }
    assert canonicalize_chart_patch({"color": "red"}, {"hue": "bolge"}) == {
        "hue": "bolge", "color": None,
    }
    # kind is normalized, not merely passed through
    assert canonicalize_chart_patch({}, {"kind": "  BAR "}) == {"kind": "bar"}
    # deleting a key that was never set is not a revision
    assert canonicalize_chart_patch({}, {}, ["title"]) == {}


# ── title: one source of truth ───────────────────────────────────────────────

def test_a_title_revision_moves_both_the_spec_and_the_header(store, workspace, plots):
    """The prompt showed `chart:ab12c3 v2 "Eski Başlık"` on one line and
    `title=Yeni Başlık` on the next, and the model had no way to tell which
    was real."""
    obj = _chart(store, workspace, title="Eski Başlık")
    chart_revise(**_base(store, workspace, plots), changes={"title": "Yeni Başlık"})

    updated = store.get(obj.id)
    assert updated.spec["title"] == "Yeni Başlık"
    assert updated.title == "Yeni Başlık"
    assert "Eski Başlık" not in updated.render()


def test_clearing_the_title_clears_the_header_too(store, workspace, plots):
    obj = _chart(store, workspace, title="Eski Başlık")
    chart_revise(**_base(store, workspace, plots), changes={}, clear_fields=["title"])

    updated = store.get(obj.id)
    assert "title" not in updated.spec
    assert updated.title == ""


def test_a_create_time_fallback_title_survives_an_unrelated_revision(store, workspace, plots):
    """`register_chart` invents "satis / ay" when the user named no title, and
    that never enters the spec -- an unrelated patch must not wipe it."""
    obj = _chart(store, workspace)
    assert obj.title
    chart_revise(**_base(store, workspace, plots), changes={"kind": "bar"})
    assert store.get(obj.id).title == obj.title


# ── clear_fields: the store always supported deletion ────────────────────────

@pytest.mark.parametrize("field", ["title", "color", "hue"])
def test_clear_fields_actually_deletes(store, workspace, plots, field):
    obj = _chart(store, workspace, title="Başlık", color="kırmızı")
    if field == "hue":
        chart_revise(**_base(store, workspace, plots), changes={"hue": "bolge"})
    assert field in store.get(obj.id).spec

    out = chart_revise(**_base(store, workspace, plots), changes={}, clear_fields=[field])

    assert not out.startswith("[ERROR]"), out
    assert field not in store.get(obj.id).spec


def test_clearing_hue_is_not_validated_as_a_column_name(store, workspace, plots):
    """"hue kullanma" must not be answered with "there is no column called
    None" -- a deletion names no column."""
    obj = _chart(store, workspace)
    chart_revise(**_base(store, workspace, plots), changes={"hue": "bolge"})

    out = chart_revise(**_base(store, workspace, plots), changes={}, clear_fields=["hue"])

    assert not out.startswith("[ERROR]"), out
    assert "hue" not in store.get(obj.id).spec


def test_an_unknown_clear_field_is_refused_by_name(store, workspace, plots):
    _chart(store, workspace)
    out = chart_revise(**_base(store, workspace, plots), changes={}, clear_fields=["renk"])
    assert out.startswith("[ERROR]")
    assert "renk" in out


def test_clearing_is_undoable(store, workspace, plots):
    obj = _chart(store, workspace, title="Başlık")
    chart_revise(**_base(store, workspace, plots), changes={}, clear_fields=["title"])
    assert "title" not in store.get(obj.id).spec

    working_set_control(**_base(store, workspace, plots), action="undo")
    assert store.get(obj.id).spec.get("title") == "Başlık"


# ── undo: render before commit ───────────────────────────────────────────────

def test_a_failed_undo_render_leaves_the_spec_alone(store, workspace, plots, monkeypatch):
    """Undo used to pop history, write the older spec, and only then try to
    draw it -- so a render failure left the store one version back while the
    PNG on screen was still the newer one."""
    obj = _chart(store, workspace, title="Başlık")
    chart_revise(**_base(store, workspace, plots), changes={"kind": "bar"})
    before = store.get(obj.id)
    assert before.version == 2

    monkeypatch.setattr(
        "jarvis.tools.chart_objects._render",
        lambda *a, **k: "[ERROR] renderer exploded",
    )
    out = working_set_control(**_base(store, workspace, plots), action="undo")

    after = store.get(obj.id)
    assert out.startswith("[ERROR]")
    assert after.version == before.version, "state moved despite the render failing"
    assert after.spec == before.spec
    assert len(after.revision_history) == len(before.revision_history)


def test_peek_undo_does_not_mutate(store, workspace, plots):
    obj = _chart(store, workspace)
    chart_revise(**_base(store, workspace, plots), changes={"kind": "bar"})
    before = store.get(obj.id)

    candidate, undone = store.peek_undo(obj.id)

    assert candidate is not None and candidate.get("kind") == "line"
    assert "kind" in undone
    assert store.get(obj.id).version == before.version
    assert store.get(obj.id).spec == before.spec


def test_a_successful_undo_still_reverts(store, workspace, plots):
    """The guard must not cost undo its actual job."""
    obj = _chart(store, workspace)
    chart_revise(**_base(store, workspace, plots), changes={"kind": "bar"})
    assert store.get(obj.id).spec["kind"] == "bar"

    out = working_set_control(**_base(store, workspace, plots), action="undo")

    assert not out.startswith("[ERROR]"), out
    assert store.get(obj.id).spec["kind"] == "line"


# ── the object you just edited is the one you meant ──────────────────────────

def test_explicitly_revising_an_inactive_chart_activates_it(store, workspace, plots):
    """`active(kind)` orders by is_active before updated_at, so without this the
    next bare "şimdi başlığını da değiştir" went back to the OTHER chart."""
    first = _chart(store, workspace, y="satis")
    second = _chart(store, workspace, y="gider", png="b.png")
    store.activate("c1", first.id)
    assert store.active("c1", KIND_CHART).id == first.id

    chart_revise(
        **_base(store, workspace, plots),
        changes={"color": "kırmızı"}, object_id=second.id,
    )

    assert store.active("c1", KIND_CHART).id == second.id

    # ...and the follow-up turn lands on it
    chart_revise(**_base(store, workspace, plots), changes={"title": "Gider"})
    assert store.get(second.id).spec.get("title") == "Gider"
    assert "title" not in store.get(first.id).spec


def test_a_redraw_finds_an_inactive_twin_instead_of_forking(store, workspace, plots):
    """Redraw only ever compared against the ACTIVE chart, so redrawing one the
    user had stepped away from produced a second object with the same identity."""
    original = _chart(store, workspace, y="satis")
    _chart(store, workspace, y="gider", png="b.png")     # takes over as active
    assert store.active("c1", KIND_CHART).id != original.id

    redrawn = _chart(store, workspace, y="satis", png="c.png", title="Yeniden")

    assert redrawn.id == original.id
    assert len(store.list("c1", KIND_CHART)) == 2
