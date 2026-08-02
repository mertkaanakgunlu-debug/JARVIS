"""Post-MVP Faz 4 — the Working Set's contract.

Regime A (deterministic, runs once). Everything here is store semantics,
patch/undo arithmetic, prompt rendering and routing — none of it involves a
model, so repeating it n times would measure nothing. Whether a live qwen3:8b
actually patches instead of redrawing is regime B and lives in
`scripts/revision_gate.py`.

Two groups deserve the attention:

`_same_chart` / redraw-patches — the rule that exists because of a measured
failure. Asked to change only the title, a live run called `plot_data` rather
than `chart_revise`, copied the spec out of its own prompt, omitted the colour
set one turn earlier, and then told the user the chart was still red. It was
not. These tests pin the fix at the level it was made: the CONSEQUENCE of
picking the wrong tool is gone, which is more durable than hoping for the
right pick.

The wiring group — a store with perfect semantics is worth nothing if the turn
never reads it, or reads it for the wrong conversation. MEMORY.md's
verify-the-guard-is-on-the-path lesson, applied to a feature whose whole value
is being on the path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.graph.role_router import FAST, REASONING, RoleDecision
from jarvis.graph.tool_router import ToolRoute, classify_query, with_active_object
from jarvis.tools.chart_objects import (
    _same_chart,
    chart_revise,
    register_chart,
    working_set_control,
)
from jarvis.working_set import (
    KIND_CHART,
    MAX_PROMPT_CHARS,
    WorkingSetStore,
    render_block,
)

CSV = "ay,satis,gider\n2026-01,120,80\n2026-02,145,90\n2026-03,132,85\n"


@pytest.fixture
def workspace(tmp_path) -> Path:
    (tmp_path / "satis.csv").write_text(CSV, encoding="utf-8")
    return tmp_path


@pytest.fixture
def store(tmp_path) -> WorkingSetStore:
    s = WorkingSetStore(tmp_path / "ws.db")
    yield s
    s.close()


def _base(store, workspace):
    return {"conversation_id": "c1", "store": store, "workspace": workspace}


def _chart(store, workspace, **kwargs):
    defaults = {"png": "a.png", "source": "satis.csv", "x": "ay", "y": "satis", "kind": "line"}
    return register_chart(**_base(store, workspace), **{**defaults, **kwargs})


# ── Store semantics ──────────────────────────────────────────────────────────

def test_a_patch_bumps_the_version_and_records_its_inverse(store):
    obj = store.create("c1", KIND_CHART, {"kind": "line", "x": "ay"})
    patched = store.patch(obj.id, {"color": "red"})
    assert patched.version == 2
    assert patched.spec["color"] == "red"
    assert patched.revision_history[-1].before == {"color": None}


def test_a_patch_that_changes_nothing_is_not_a_revision(store):
    """A history of no-ops would make `undo` walk backwards through changes
    that changed nothing, which reads to the user as undo being broken."""
    obj = store.create("c1", KIND_CHART, {"color": "red"})
    same = store.patch(obj.id, {"color": "red"})
    assert same.version == 1
    assert same.revision_history == ()


def test_none_removes_a_key_rather_than_emptying_it(store):
    """Some fields genuinely exclude each other (a chart's hue and color do, at
    the renderer). A key left at None would still render as a real setting in
    the prompt block and be re-sent to the renderer."""
    obj = store.create("c1", KIND_CHART, {"color": "red", "kind": "line"})
    patched = store.patch(obj.id, {"color": None})
    assert "color" not in patched.spec
    assert patched.spec["kind"] == "line"


def test_undo_walks_backwards_one_step_at_a_time(store):
    obj = store.create("c1", KIND_CHART, {"kind": "line"})
    store.patch(obj.id, {"color": "red"})
    store.patch(obj.id, {"title": "T"})

    reverted, undone = store.undo(obj.id)
    assert reverted.version == 2 and "title" not in reverted.spec
    assert "title" in undone
    assert reverted.spec["color"] == "red"

    reverted, _undone = store.undo(obj.id)
    assert reverted.version == 1 and "color" not in reverted.spec


def test_undo_at_the_first_version_is_a_no_op_not_an_error(store):
    obj = store.create("c1", KIND_CHART, {"kind": "line"})
    reverted, undone = store.undo(obj.id)
    assert reverted.version == 1 and undone == ""


def test_a_key_that_did_not_exist_before_is_removed_by_undo_not_nulled(store):
    """A spec carrying `color=None` would render as a real setting."""
    obj = store.create("c1", KIND_CHART, {"kind": "line"})
    store.patch(obj.id, {"color": "red"})
    reverted, _ = store.undo(obj.id)
    assert "color" not in reverted.spec


def test_conversations_cannot_see_or_activate_each_others_objects(store):
    """The reason this store is keyed at all: one shared JarvisAgent serves
    every client, so an agent-level attribute would let conversation A's
    revision land on B's chart."""
    mine = store.create("c1", KIND_CHART, {"kind": "line"})
    store.create("c2", KIND_CHART, {"kind": "bar"})
    assert [o.id for o in store.list("c1")] == [mine.id]
    assert store.activate("c2", mine.id) is None
    assert store.active("c1").id == mine.id


def test_creating_an_object_makes_it_the_active_one(store):
    store.create("c1", KIND_CHART, {"kind": "line"})
    second = store.create("c1", KIND_CHART, {"kind": "bar"})
    assert store.active("c1").id == second.id


# ── The prompt block ─────────────────────────────────────────────────────────

def test_an_empty_working_set_costs_zero_tokens():
    """Most turns, most conversations. A "(none)" placeholder on every request
    would be a permanent tax for a feature that is usually not in use."""
    assert render_block([]) == ""


def test_the_block_marks_the_active_object_and_carries_its_spec(store):
    store.create("c1", KIND_CHART, {"kind": "line", "x": "ay"})
    active = store.create("c1", KIND_CHART, {"kind": "bar", "x": "ay", "color": "red"})
    block = render_block(store.list("c1"))
    assert "AKTİF → " + active.ref in block
    assert "color=red" in block


def test_metadata_keys_are_not_presented_as_knobs(store):
    """`_columns` describes the object; it does not configure it. Rendering it
    among the settings would invite a revision that tries to change it."""
    store.create("c1", KIND_CHART, {"kind": "line", "_columns": "ay, satis"})
    block = render_block(store.list("c1"))
    assert "columns: ay, satis" in block
    assert "_columns=" not in block


def test_the_block_is_bounded_and_says_what_it_dropped(store):
    """Injected on EVERY turn, so it has to be bounded by construction rather
    than by hoping specs stay small. Silent truncation would make "why did it
    forget my other chart" unanswerable from the prompt."""
    for i in range(40):
        store.create("c1", KIND_CHART, {"kind": "line", "title": f"chart number {i} " + "x" * 80})
    block = render_block(store.list("c1"))
    assert len(block) <= MAX_PROMPT_CHARS
    assert "gösterilmedi" in block


# ── Routing: an active object claims an unclassifiable turn ──────────────────

@pytest.mark.parametrize("query", ["rengini kırmızı yap", "biraz daha büyük olsun", "eski haline getir"])
def test_a_bare_revision_matches_no_domain_on_its_own(query):
    """The premise of the whole rule. If these already routed somewhere, state
    would not be needed -- and guessing revision vocabulary was the tempting
    fix this module has already had to delete three generic verbs for."""
    assert classify_query(query).primary_domain == "conversation"


@pytest.mark.parametrize("query", ["rengini kırmızı yap", "eski haline getir", "teşekkürler"])
def test_an_active_chart_claims_a_turn_that_matched_nothing(query):
    routed = with_active_object(classify_query(query), KIND_CHART)
    assert routed.primary_domain == "data"


@pytest.mark.parametrize("query,expected", [
    ("hava durumu nasıl", "weather"),
    ("son 3 mailimi listele", "mail"),
    ("bugün neler var", "briefing"),
])
def test_a_turn_that_named_what_it_wanted_is_left_alone(query, expected):
    """Applying the rule to every turn would attach the chart domain to
    "hava durumu nasıl", making it multi-domain and therefore reasoning-tier --
    a latency tax on every turn for the rest of the conversation."""
    route = classify_query(query)
    assert with_active_object(route, KIND_CHART).primary_domain == expected


def test_an_unknown_kind_changes_nothing():
    route = classify_query("rengini kırmızı yap")
    assert with_active_object(route, "sculpture") is route


def test_the_rule_never_removes_a_domain():
    route = ToolRoute("mail", ["mail", "files"], 0.6, False)
    assert set(with_active_object(route, KIND_CHART).domains) >= {"mail"}


# ── The redraw-patches rule (the measured failure) ───────────────────────────

def test_same_chart_is_identity_not_presentation():
    spec = {"source": "a.csv", "x": "ay", "y": "satis", "kind": "line", "color": "red"}
    assert _same_chart(spec, {"source": "a.csv", "x": "ay", "y": "satis", "kind": "bar"})
    assert not _same_chart(spec, {"source": "a.csv", "x": "ay", "y": "gider"})
    assert not _same_chart(spec, {"source": "b.csv", "x": "ay", "y": "satis"})


def test_a_redraw_that_omits_a_field_keeps_it(store, workspace):
    """THE regression. A live run redrew with the full spec copied out of its
    own prompt, omitted the colour set one turn earlier, and then told the user
    the chart was still red."""
    first = _chart(store, workspace, title="İlk")
    store.patch(first.id, {"color": "kırmızı"})

    redrawn = _chart(store, workspace, png="b.png", title="2026 Satışları")
    assert redrawn.id == first.id, "a redraw of the same chart must not create a second one"
    assert redrawn.spec["color"] == "kırmızı"
    assert redrawn.spec["title"] == "2026 Satışları"


def test_a_genuinely_different_chart_from_the_same_file_is_a_new_object(store, workspace):
    """The other side of the rule: "bir de gider grafiği çiz" names a different
    y, so it must not overwrite the first chart."""
    _chart(store, workspace)
    _chart(store, workspace, png="b.png", y="gider")
    assert len(store.list("c1")) == 2


def test_hue_and_colour_never_coexist_in_a_stored_spec(store, workspace):
    """generate_plot ignores `color` when `hue` is set, so a spec holding both
    states something untrue about its own output -- and this spec is injected
    into the prompt every turn until it is believed."""
    obj = _chart(store, workspace)
    store.patch(obj.id, {"color": "red"})
    with_hue = _chart(store, workspace, png="b.png", hue="gider")
    assert "color" not in with_hue.spec and with_hue.spec["hue"] == "gider"

    back_to_colour = _chart(store, workspace, png="c.png", color="blue")
    assert "hue" not in back_to_colour.spec and back_to_colour.spec["color"] == "blue"


def test_a_render_with_no_conversation_registers_nothing(store, workspace):
    """A workflow step or a background render is not part of anyone's
    conversation; inventing an owner would put a chart into a transcript that
    never mentioned one."""
    assert register_chart(
        conversation_id="", store=store, workspace=workspace,
        png="a.png", source="satis.csv", x="ay", y="satis",
    ) is None
    assert store.list("") == []


def test_the_real_columns_are_stored_for_the_next_revision(store, workspace):
    obj = _chart(store, workspace)
    assert obj.spec["_columns"] == "ay, satis, gider"


# ── chart_revise ─────────────────────────────────────────────────────────────

def test_a_revision_changes_only_what_it_names(store, workspace, tmp_path):
    _chart(store, workspace, title="İlk")
    out = chart_revise(
        **_base(store, workspace), plots_dir=tmp_path / "p", changes={"color": "kırmızı"},
    )
    assert not out.startswith("[ERROR]"), out
    spec = store.active("c1", KIND_CHART).spec
    assert spec["color"] == "kırmızı"
    assert spec["title"] == "İlk" and spec["x"] == "ay" and spec["y"] == "satis"


def test_revising_a_column_that_does_not_exist_returns_the_schema(store, workspace, tmp_path):
    """Schema-first, at the boundary where invented columns actually bite. The
    model has never seen the file, so the correction has to come back with the
    refusal or the next attempt is another guess."""
    _chart(store, workspace)
    out = chart_revise(
        **_base(store, workspace), plots_dir=tmp_path / "p", changes={"y": "ciro"},
    )
    assert out.startswith("[ERROR]")
    assert "gider" in out and "satis" in out


def test_a_turkish_spelling_of_a_real_column_is_accepted(store, workspace, tmp_path):
    """The user says "satış", the file says "satis". Refusing that would fail
    on the single most Turkish thing about this data."""
    _chart(store, workspace, y="gider")
    out = chart_revise(
        **_base(store, workspace), plots_dir=tmp_path / "p", changes={"y": "satış"},
    )
    assert not out.startswith("[ERROR]"), out
    assert store.active("c1", KIND_CHART).spec["y"] == "satis"


def test_revising_with_no_chart_says_so_instead_of_failing_obscurely(store, workspace, tmp_path):
    out = chart_revise(
        **_base(store, workspace), plots_dir=tmp_path / "p", changes={"color": "red"},
    )
    assert out.startswith("[ERROR]") and "grafik yok" in out


def test_an_unsupported_chart_kind_is_refused_with_the_options(store, workspace, tmp_path):
    _chart(store, workspace)
    out = chart_revise(
        **_base(store, workspace), plots_dir=tmp_path / "p", changes={"kind": "pasta"},
    )
    assert out.startswith("[ERROR]") and "line" in out
    assert store.active("c1", KIND_CHART).spec["kind"] == "line"


def test_a_revision_that_cannot_render_rolls_the_object_back(store, workspace, tmp_path, monkeypatch):
    """A stored spec that cannot draw would be injected into every later prompt
    and would break the NEXT revision too — one bad argument becoming a stuck
    conversation."""
    _chart(store, workspace)
    import jarvis.tools.chart_objects as chart_objects

    monkeypatch.setattr(chart_objects, "_render", lambda *a, **k: "[ERROR] render patladı")
    out = chart_revise(
        **_base(store, workspace), plots_dir=tmp_path / "p", changes={"color": "red"},
    )
    assert out.startswith("[ERROR]")
    obj = store.active("c1", KIND_CHART)
    assert obj.version == 1 and "color" not in obj.spec


# ── working_set control ──────────────────────────────────────────────────────

def test_undo_redraws_the_chart_it_reverted(store, workspace, tmp_path):
    _chart(store, workspace)
    chart_revise(**_base(store, workspace), plots_dir=tmp_path / "p", changes={"kind": "bar"})
    out = working_set_control(
        **_base(store, workspace), plots_dir=tmp_path / "p", action="undo",
    )
    assert not out.startswith("[ERROR]"), out
    assert store.active("c1", KIND_CHART).spec["kind"] == "line"


def test_activate_switches_which_chart_a_bare_revision_edits(store, workspace, tmp_path):
    first = _chart(store, workspace)
    _chart(store, workspace, png="b.png", y="gider")
    working_set_control(
        **_base(store, workspace), plots_dir=tmp_path / "p",
        action="activate", object_id=first.id,
    )
    assert store.active("c1").id == first.id


def test_list_on_an_empty_conversation_is_a_sentence_not_a_crash(store, workspace, tmp_path):
    out = working_set_control(**_base(store, workspace), plots_dir=tmp_path / "p", action="list")
    assert "nesne yok" in out


# ── Wiring: is the guard on the path? ────────────────────────────────────────

class _StubAgent:
    """Just enough JarvisAgent to exercise the real _working_set_turn."""

    def __init__(self, store):
        self.working_set = store

    from jarvis.agent import JarvisAgent as _Real
    _working_set_turn = _Real._working_set_turn


def test_the_turn_hook_reads_the_conversation_it_was_given(store):
    """Keyed on the conversation passed in, never on whichever session happens
    to be loaded — one shared agent serves every client."""
    store.create("other", KIND_CHART, {"kind": "line"})
    agent = _StubAgent(store)
    route = classify_query("rengini kırmızı yap")
    decision = RoleDecision(FAST, "conversation")

    _r, _d, block = agent._working_set_turn("mine", route, decision, "x", False)
    assert block == ""

    _r, _d, block = agent._working_set_turn("other", route, decision, "x", False)
    assert "AKTİF" in block


def test_a_claimed_turn_gets_its_role_recomputed(store):
    """A turn that just stopped being `conversation` no longer earns the fast
    tier for being one. Missing this would send a revision to a non-thinking
    model — the shape that made weather invent "32°C" in Faz 3."""
    store.create("c1", KIND_CHART, {"kind": "line"})
    agent = _StubAgent(store)
    route = classify_query("rengini kırmızı yap")
    assert route.primary_domain == "conversation"

    new_route, decision, _block = agent._working_set_turn(
        "c1", route, RoleDecision(FAST, "conversation"), "rengini kırmızı yap", False,
    )
    assert new_route.primary_domain == "data"
    assert decision.role == REASONING


def test_an_unreadable_working_set_degrades_instead_of_ending_the_turn(store):
    class _Broken:
        def list(self, _conversation):
            raise RuntimeError("db gone")

    agent = _StubAgent(_Broken())
    route = classify_query("merhaba")
    decision = RoleDecision(FAST, "conversation")
    assert agent._working_set_turn("c1", route, decision, "merhaba", False) == (route, decision, "")


def test_no_conversation_id_reads_nothing(store):
    store.create("c1", KIND_CHART, {"kind": "line"})
    agent = _StubAgent(store)
    route = classify_query("merhaba")
    decision = RoleDecision(FAST, "conversation")
    assert agent._working_set_turn("", route, decision, "merhaba", False)[2] == ""


# ── Schema-first column fitting ──────────────────────────────────────────────

def test_an_invented_column_is_corrected_and_the_substitution_is_reported(workspace):
    """The measured failure that made this exist. Left to fail, the model asked
    for a column named 'Tarih', got an error listing the three real ones, and
    never drew anything — killing 3 of 5 live revision chains at turn 0."""
    from jarvis.tools.chart_objects import fit_columns

    x, y, hue, notes = fit_columns(workspace / "satis.csv", "", "Tarih", "satis", "")
    assert x == "ay" and y == "satis" and hue == ""
    assert any("Tarih" in n for n in notes), "a substitution must be visible"


def test_a_turkish_spelling_is_not_reported_as_a_substitution(workspace):
    """"satış" and "satis" are one column written two ways. Reporting that as a
    substitution would train the reader to ignore the notes."""
    from jarvis.tools.chart_objects import fit_columns

    _x, y, _hue, notes = fit_columns(workspace / "satis.csv", "", "ay", "satış", "")
    assert y == "satis"
    assert notes == []


def test_an_invented_hue_is_dropped_rather_than_substituted(workspace):
    """A grouping nobody asked for is worse than no grouping — unlike x/y,
    there is no chart without which the request cannot be answered."""
    from jarvis.tools.chart_objects import fit_columns

    _x, _y, hue, notes = fit_columns(workspace / "satis.csv", "", "ay", "satis", "bölge")
    assert hue == ""
    assert any("hue" in n for n in notes)


def test_an_unreadable_file_leaves_the_columns_alone(tmp_path):
    """A chart that might work beats a refusal built on a failed side lookup."""
    from jarvis.tools.chart_objects import fit_columns

    missing = tmp_path / "nope.csv"
    assert fit_columns(missing, "", "a", "b", "c") == ("a", "b", "c", [])
