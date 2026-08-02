"""Charts as revisable objects (Post-MVP Faz 4).

`plot_data` drew a chart and forgot it. That is the right primitive for "draw
me this" and the wrong one for a conversation, because the next thing a person
says is *"çizgiyi kırmızı yap"* — and by then there was no chart, only a PNG on
disk whose parameters nobody kept.

**There is still exactly one tool that draws a chart.** The first version of
this phase added a second (`chart_new`) alongside `plot_data` and let the model
choose; a live run chose `plot_data`, no object was created, and every one of
the six following revision turns failed. That is this codebase's most expensive
recurring failure — the model picking the wrong member of an ambiguous pair —
and the fix is not a better docstring, it is not having the pair. `plot_data`
keeps its name, arguments and return value, and now also registers what it drew.

What is left here:

  * ``register_chart`` — called by `plot_data` after a successful render.
    Stores the spec plus the source's real column names, so the next turn's
    revision can check a column before naming it.
  * ``chart_revise``   — a partial patch. Fields not named keep their value;
    that is the entire point, and it is what makes the measured "one argument
    per turn" limit sufficient instead of crippling.
  * ``working_set``    — list / show / activate / undo across every kind.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis.tools.data_analysis import describe_schema, render_schema
from jarvis.tools.plotting import SUPPORTED_KINDS, generate_plot
from jarvis.working_set import (
    KIND_CHART,
    WorkingObject,
    WorkingSetStore,
)

# Spec keys a chart understands. Anything else is refused rather than stored:
# a spec that accumulates keys the renderer ignores would show the user a
# setting that does nothing, which is a lie the prompt then repeats every turn.
CHART_FIELDS = ("source", "kind", "x", "y", "hue", "title", "color", "sheet")


def _resolve(source: str, workspace: Path) -> Path:
    path = Path(source)
    return path if path.is_absolute() else (workspace / source)


def _render(spec: dict[str, Any], workspace: Path, plots_dir: Path) -> str:
    """Spec → PNG path, or an [ERROR] string. One place, so create and revise
    cannot drift into producing different charts from the same spec."""
    return generate_plot(
        path=_resolve(str(spec.get("source") or ""), workspace),
        kind=str(spec.get("kind") or "line"),
        x=str(spec.get("x") or ""),
        y=str(spec.get("y") or ""),
        title=str(spec.get("title") or ""),
        hue=str(spec.get("hue") or ""),
        output="",
        plots_dir=plots_dir,
        sheet=str(spec.get("sheet") or ""),
        color=str(spec.get("color") or ""),
    )


def _describe(obj: WorkingObject, png: str, schema_text: str = "", note: str = "") -> str:
    lines = [f"[Chart] {obj.ref} v{obj.version}" + (f' — "{obj.title}"' if obj.title else "")]
    if note:
        lines.append(note)
    lines.append("spec: " + ", ".join(f"{k}={v}" for k, v in obj.spec.items() if v not in (None, "")))
    lines.append(f"PNG: {png}")
    if schema_text:
        lines.append(schema_text)
    lines.append(
        "Değiştirmek için: chart_revise(color=...) / chart_revise(kind='bar') — "
        "yalnız değişeni gönder. Geri almak için: working_set('undo')."
    )
    return "\n".join(lines)


def normalize(spec: dict[str, Any]) -> dict[str, Any]:
    """Keep a spec describing the chart that will actually render.

    `hue` and `color` are mutually exclusive at the renderer (hue means colour
    encodes a column; a single colour would silently destroy that encoding, so
    generate_plot ignores `color` whenever `hue` is set). A spec holding both
    therefore states something untrue about its own output — and this spec is
    injected into the model's prompt every turn, so an untrue field is repeated
    until it is believed. Whichever was set LAST wins and the other is dropped,
    which is also what the user asked for: "make it red" on a grouped chart is
    a request to stop grouping by colour.
    """
    spec = {k: v for k, v in spec.items() if v not in (None, "")}
    return spec


def _merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """base + incoming, with the hue/color exclusion applied to `incoming`."""
    merged = dict(base)
    for key, value in incoming.items():
        if value in (None, ""):
            continue
        if key == "hue":
            merged.pop("color", None)
        elif key == "color":
            merged.pop("hue", None)
        merged[key] = value
    return normalize(merged)


def _same_chart(spec: dict[str, Any], incoming: dict[str, Any]) -> bool:
    """Is `incoming` a redraw of `spec`, or a different chart?

    Identity is (source, x, y). Everything else -- kind, title, colour, hue --
    is presentation, and changing presentation is what a revision IS.

    This exists because of a measured failure. Asked to change only the title,
    a live run called `plot_data` rather than `chart_revise`, copying the spec
    out of its own prompt and omitting the colour set one turn earlier. It then
    told the user the chart was still red. It was not. Making that call PATCH
    instead of CREATE removes the consequence of the model picking the wrong
    tool, which is more durable than trying to make it pick the right one --
    the first cut of this phase already tried the docstring route and lost.

    A genuinely new chart from the same file ("bir de gider grafiği çiz")
    names a different y, so it still creates its own object.
    """
    for key in ("source", "x", "y"):
        if str(spec.get(key, "")).strip() != str(incoming.get(key, "")).strip():
            return False
    return True


def register_chart(
    *,
    conversation_id: str,
    store: WorkingSetStore,
    workspace: Path,
    png: str,
    source: str = "",
    x: str = "",
    y: str = "",
    kind: str = "line",
    title: str = "",
    hue: str = "",
    color: str = "",
    sheet: str = "",
) -> WorkingObject | None:
    """Keep a chart `plot_data` just drew, so the next turn can patch it.

    Called AFTER a successful render, never instead of one: a working-set
    object whose spec cannot draw would be injected into every later prompt as
    if it were a real chart, and would break the next revision too.

    Returns None (silently) when there is no conversation to key on — a
    workflow step or a background render is not part of anyone's conversation,
    and inventing an owner for it would put a chart into a transcript that
    never mentioned one.

    Reads the source's real columns and stores them as `_columns`. That is
    where the plan's "schema first" rule pays: on the FIRST draw the model can
    already see the columns in `generate_plot`'s error if it guesses wrong, but
    on a REVISION three turns later it has nothing — except this line, now in
    its prompt.
    """
    if not conversation_id:
        return None

    incoming = normalize({
        "source": str(source), "kind": kind, "x": x, "y": y,
        "hue": hue, "title": title, "color": color, "sheet": sheet,
    })

    if source:
        path = _resolve(source, workspace)
        if path.exists():
            schema, err = describe_schema(path, sheet)
            if not err and schema:
                incoming["_columns"] = ", ".join(schema["columns"])

    try:
        active = store.active(conversation_id, KIND_CHART)
        # A redraw of the SAME chart patches it. See _same_chart for the live
        # run this rule exists for -- and note what it preserves: fields the
        # model did NOT restate, which is precisely what a redraw loses and
        # what a working set is for.
        if active is not None and _same_chart(active.spec, incoming):
            merged = _merge(active.spec, incoming)
            changes = {
                key: merged.get(key)          # absent in merged ⇒ None ⇒ removed
                for key in set(merged) | set(active.spec)
                if active.spec.get(key) != merged.get(key)
            }
            obj = store.patch(active.id, changes) if changes else active
            if obj is not None:
                store.record_artifact(obj.id, png)
            return obj

        obj = store.create(
            conversation_id, KIND_CHART, incoming,
            title=title or (f"{y} / {x}" if y or x else "grafik"),
            source_artifacts=(png,),
        )
    except Exception:  # noqa: BLE001 -- registration must never fail a drawn chart
        return None
    return obj


def fit_columns(
    path: Path,
    sheet: str,
    x: str,
    y: str,
    hue: str,
) -> tuple[str, str, str, list[str]]:
    """Requested columns → columns the file actually has. Returns (x, y, hue, notes).

    The plan's "schema first" rule, and it had to be measured into existence.
    The first cut of this phase left `plot_data` to fail on an unknown column,
    reasoning that its error already lists the real ones so the model could
    retry. It does list them. The model does not retry: in a live 5-chain run
    it asked for a column named 'Tarih' (there is no such column), got
    `[ERROR] Column 'Tarih' not found. Available: ['ay','satis','gider']`,
    answered the user, and never drew anything — killing 3 of 5 revision
    chains at turn 0 with a working set that never got an object to hold.

    So the correction happens here instead of being offered. Three steps, each
    reported: exact match, then a diacritic/case fold ("satış" → "satis"), then
    a dtype-appropriate default. The model has never seen the file, so an
    invented column is not a mistake it could have avoided — but drawing the
    wrong column silently WOULD be a mistake, which is why every substitution
    comes back in the notes.

    Returns everything unchanged if the schema cannot be read: a chart that
    might work beats a refusal built on a failed side lookup.
    """
    schema, err = describe_schema(path, sheet)
    if err or not schema:
        return x, y, hue, []

    columns = schema["columns"]
    numeric = schema["numeric"]
    categorical = schema["categorical"]
    notes: list[str] = []

    def fit(requested: str, preferred: list[str], role: str, droppable: bool) -> str:
        name = (requested or "").strip()
        if name in columns:
            return name
        if name:
            hit = _resolve_column(name, columns)
            if hit:
                return hit          # a spelling difference is not a substitution
            if droppable:
                notes.append(f"{role}='{name}' diye bir kolon yok, yok sayıldı")
                return ""
            if preferred:
                notes.append(f"{role}='{name}' diye bir kolon yok, '{preferred[0]}' kullanıldı")
                return preferred[0]
            notes.append(f"{role}='{name}' diye bir kolon yok")
            return ""
        return ""

    return (
        fit(x, categorical or columns, "x", droppable=False),
        fit(y, numeric or columns, "y", droppable=False),
        fit(hue, [], "hue", droppable=True),
        notes,
    )


def _resolve_column(requested: str, columns: list[str]) -> str:
    """A requested column name → the file's actual one, or "" if there is none.

    Exact match, then a diacritic/case-folded one. The fold matters more than
    it looks: the user says "satış", the file says "satis", and refusing that
    revision would fail on the single most Turkish thing about this data. It
    can only ever map to a column that exists, so it cannot invent one.
    """
    from jarvis.nlu.temporal import fold

    name = (requested or "").strip()
    if name in columns:
        return name
    return {fold(c): c for c in columns}.get(fold(name), "")


def chart_revise(
    *,
    conversation_id: str,
    store: WorkingSetStore,
    workspace: Path,
    plots_dir: Path,
    changes: dict[str, Any],
    object_id: str = "",
) -> str:
    """Patch the active chart with only the named fields and re-render."""
    obj = store.get(object_id) if object_id else store.active(conversation_id, KIND_CHART)
    if obj is None:
        return (
            "[ERROR] Düzenlenecek bir grafik yok. Önce plot_data ile çizin "
            "(mevcut nesneler: working_set('list'))."
        )
    if obj.conversation_id != conversation_id:
        return "[ERROR] Bu nesne bu konuşmaya ait değil."

    changes = {k: v for k, v in changes.items() if k in CHART_FIELDS and v not in (None, "")}
    if not changes:
        return (
            "[ERROR] Hiçbir alan verilmedi. Değiştirilebilir alanlar: "
            f"{', '.join(CHART_FIELDS)}."
        )

    if "kind" in changes:
        kind = str(changes["kind"]).strip().lower()
        if kind not in SUPPORTED_KINDS:
            return f"[ERROR] kind '{kind}' desteklenmiyor. Seçenekler: {sorted(SUPPORTED_KINDS)}"
        changes["kind"] = kind

    # Column changes are validated against the file, not accepted on faith.
    # A revision that renames y to a column that does not exist would
    # otherwise store a spec that cannot render -- which breaks not just this
    # turn but every later revision, since the stored spec is the base.
    if {"x", "y", "hue", "source"} & set(changes):
        merged = {**obj.spec, **changes}
        path = _resolve(str(merged.get("source") or ""), workspace)
        if not path.exists():
            return f"[ERROR] Veri dosyası bulunamadı: {path}"
        schema, err = describe_schema(path, str(merged.get("sheet") or ""))
        if err:
            return err
        for role in ("x", "y", "hue"):
            if role in changes:
                resolved = _resolve_column(str(changes[role]), schema["columns"])
                if not resolved:
                    return (
                        f"[ERROR] '{changes[role]}' diye bir kolon yok.\n"
                        + render_schema(schema, path.name)
                    )
                changes[role] = resolved

    before_version = obj.version
    patched = store.patch(obj.id, changes)
    if patched is None:
        return "[ERROR] Nesne kayboldu."
    if patched.version == before_version:
        return (
            f"[Chart] {patched.ref} zaten istenen durumda (v{patched.version}) — "
            "değişiklik gerekmedi."
        )

    png = _render(patched.spec, workspace, plots_dir)
    if png.startswith("[ERROR]"):
        # Roll the object back: a stored spec that cannot render would be
        # injected into every later prompt and would break the NEXT revision
        # too, turning one bad argument into a stuck conversation.
        store.undo(patched.id)
        return f"{png}\n(Değişiklik geri alındı, grafik v{before_version} olarak kaldı.)"

    store.record_artifact(patched.id, png)
    applied = ", ".join(f"{k}={v}" for k, v in changes.items())
    return _describe(patched, png, note=f"Değişen: {applied}")


def working_set_control(
    *,
    conversation_id: str,
    store: WorkingSetStore,
    workspace: Path,
    plots_dir: Path,
    action: str,
    object_id: str = "",
) -> str:
    """list | show | activate | undo, across every object kind."""
    action = (action or "list").strip().lower()

    if action == "list":
        objects = store.list(conversation_id)
        if not objects:
            return "[WorkingSet] Bu konuşmada düzenlenebilir nesne yok."
        lines = [f"[WorkingSet] {len(objects)} nesne:"]
        for obj in objects:
            lines.append(("AKTİF → " if obj.is_active else "        ") + obj.render())
        return "\n".join(lines)

    if action == "show":
        obj = store.get(object_id) if object_id else store.active(conversation_id)
        if obj is None or obj.conversation_id != conversation_id:
            return "[ERROR] Nesne bulunamadı."
        lines = [f"[WorkingSet] {obj.render()}"]
        if obj.revision_history:
            lines.append("Revizyon geçmişi (en yeni sonda):")
            for rev in obj.revision_history:
                lines.append(f"  v{rev.version}: {rev.describe()}")
        return "\n".join(lines)

    if action == "activate":
        if not object_id:
            return "[ERROR] 'object_id' gerekli. Listelemek için: working_set('list')."
        obj = store.activate(conversation_id, object_id.split(":")[-1])
        if obj is None:
            return f"[ERROR] '{object_id}' bu konuşmada bulunamadı."
        return f"[WorkingSet] Aktif nesne artık {obj.ref}.\n{obj.render()}"

    if action == "undo":
        obj = store.get(object_id) if object_id else store.active(conversation_id)
        if obj is None or obj.conversation_id != conversation_id:
            return "[ERROR] Geri alınacak nesne yok."
        if not obj.revision_history:
            return f"[WorkingSet] {obj.ref} zaten ilk halinde (v{obj.version}) — geri alınacak bir şey yok."
        reverted, undone = store.undo(obj.id)
        if reverted is None:
            return "[ERROR] Geri alınamadı."
        if reverted.kind != KIND_CHART:
            return f"[WorkingSet] {reverted.ref} v{reverted.version}'e döndü (geri alınan: {undone})."
        png = _render(reverted.spec, workspace, plots_dir)
        if png.startswith("[ERROR]"):
            return f"{png}\n(Spec v{reverted.version}'e döndü ama yeniden çizilemedi.)"
        store.record_artifact(reverted.id, png)
        return _describe(reverted, png, note=f"Geri alındı: {undone}")

    return f"[ERROR] Bilinmeyen action '{action}'. Geçerli: list | show | activate | undo."
