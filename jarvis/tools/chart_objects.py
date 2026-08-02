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

import logging
from pathlib import Path
from typing import Any

from jarvis.tools.data_analysis import describe_schema, render_schema
from jarvis.tools.plotting import SUPPORTED_KINDS, generate_plot
from jarvis.working_set import (
    KIND_CHART,
    WorkingObject,
    WorkingSetStore,
)

logger = logging.getLogger(__name__)

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


def canonicalize_chart_patch(
    current: dict[str, Any],
    requested: dict[str, Any],
    clear: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Requested changes → the changes that will actually be applied.

    THE single mutation path for a chart spec. GPT review (Faz 5 hazırlığı):
    the hue/color exclusion below existed only on the redraw path
    (`register_chart`'s merge), while `chart_revise` patched the store
    directly. So "rengi kırmızı yap" on a chart grouped by `hue` stored BOTH
    -- the renderer ignores `color` whenever `hue` is set, so the chart did not
    turn red, while the prompt told the model every turn that it had. That is
    exactly the inconsistency this module's comments say the rule exists to
    prevent, reappearing on the path a user actually takes. Both callers now
    come through here.

    `clear` names fields to DELETE (mapped to None, which the store reads as
    "remove the key"). The store always supported deletion; nothing
    model-facing could reach it, so "başlığı kaldır" / "gruplamayı kaldır"
    were unaskable.
    """
    changes: dict[str, Any] = {}

    for key in clear:
        name = str(key or "").strip().lower()
        if name in CHART_FIELDS:
            changes[name] = None

    for key, value in requested.items():
        if key not in CHART_FIELDS or value in (None, ""):
            continue
        text = str(value).strip()
        if not text:
            continue
        if key == "kind":
            text = text.lower()
        # Mutually exclusive at the renderer: setting one deletes the other,
        # and whichever is named LAST wins.
        if key == "hue":
            changes["color"] = None
        elif key == "color":
            changes["hue"] = None
        changes[key] = text

    # Deleting a key that was never there is a no-op, not a revision: it would
    # otherwise bump the version and give `undo` a step that undoes nothing.
    return {k: v for k, v in changes.items() if v is not None or k in current}


def _canonical_source(source: str, workspace: Path) -> str:
    """A source path reduced to its identity.

    `satis.csv`, `./satis.csv` and `C:\\...\\satis.csv` are one file and were
    three different charts, because identity compared the raw strings the model
    happened to type.
    """
    text = str(source or "").strip()
    if not text:
        return ""
    try:
        return str(_resolve(text, workspace).resolve()).casefold()
    except (OSError, ValueError):
        return text.casefold()


def _same_chart(spec: dict[str, Any], incoming: dict[str, Any], workspace: Path) -> bool:
    """Is `incoming` a redraw of `spec`, or a different chart?

    Identity is (source, sheet, x, y) with the source canonicalized. Everything
    else -- kind, title, colour, hue -- is presentation, and changing
    presentation is what a revision IS.

    This exists because of a measured failure. Asked to change only the title,
    a live run called `plot_data` rather than `chart_revise`, copying the spec
    out of its own prompt and omitting the colour set one turn earlier. It then
    told the user the chart was still red. It was not. Making that call PATCH
    instead of CREATE removes the consequence of the model picking the wrong
    tool, which is more durable than trying to make it pick the right one --
    the first cut of this phase already tried the docstring route and lost.

    `sheet` joined the key on review: one workbook's Ocak and Şubat sheets
    routinely carry the same column names, so charting both drew one chart that
    silently overwrote itself.

    A genuinely new chart from the same file ("bir de gider grafiği çiz")
    names a different y, so it still creates its own object.
    """
    if _canonical_source(spec.get("source", ""), workspace) != _canonical_source(
        incoming.get("source", ""), workspace
    ):
        return False
    if str(spec.get("sheet") or "").strip().casefold() != str(
        incoming.get("sheet") or ""
    ).strip().casefold():
        return False
    for key in ("x", "y"):
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
    extra: dict[str, Any] | None = None,
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
    # Metadata (leading underscore) describes the object rather than
    # configuring it -- WorkingObject.render() gives those their own line and
    # canonicalize_chart_patch ignores them, since they are not CHART_FIELDS.
    incoming.update(extra or {})

    if source:
        path = _resolve(source, workspace)
        if path.exists():
            schema, err = describe_schema(path, sheet)
            if not err and schema:
                incoming["_columns"] = ", ".join(schema["columns"])

    try:
        # A redraw of the SAME chart patches it. See _same_chart for the live
        # run this rule exists for -- and note what it preserves: fields the
        # model did NOT restate, which is precisely what a redraw loses and
        # what a working set is for.
        #
        # Checked against EVERY chart in the conversation, active first, not
        # only the active one (GPT review): redrawing a chart the user had
        # stepped away from used to fork a second object with the same identity,
        # so the conversation then held two charts that were the same chart.
        candidates = store.list(conversation_id, KIND_CHART)
        candidates.sort(key=lambda o: not o.is_active)
        match = next(
            (o for o in candidates if _same_chart(o.spec, incoming, workspace)), None
        )
        if match is not None:
            changes = canonicalize_chart_patch(match.spec, incoming)
            obj = store.patch(match.id, changes) if changes else match
            if obj is not None:
                store.record_artifact(obj.id, png)
                if not obj.is_active:
                    store.activate(conversation_id, obj.id)
            return obj

        obj = store.create(
            conversation_id, KIND_CHART, incoming,
            title=title or (f"{y} / {x}" if y or x else "grafik"),
            source_artifacts=(png,),
        )
    except Exception:  # noqa: BLE001 -- registration must never fail a drawn chart
        # Logged, not merely swallowed. Silence here costs the user the whole
        # feature -- the chart draws, nothing enters the working set, and every
        # later revision fails with "düzenlenecek bir grafik yok" -- with no
        # trace of why. A wiring bug hid behind this exact `return None` during
        # the Faz 5 prep (a closure captured the `working_set` TOOL instead of
        # the store, because @tool rebinds that name in make_tools' scope).
        logger.warning("chart registration failed; chart drawn but not editable", exc_info=True)
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
    clear_fields: tuple[str, ...] | list[str] = (),
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

    unknown = [
        str(f).strip().lower() for f in clear_fields
        if str(f).strip().lower() not in CHART_FIELDS
    ]
    if unknown:
        return (
            f"[ERROR] Silinemeyecek alan(lar): {', '.join(unknown)}. "
            f"Geçerli alanlar: {', '.join(CHART_FIELDS)}."
        )

    changes = canonicalize_chart_patch(obj.spec, changes, clear_fields)
    if not changes:
        return (
            "[ERROR] Hiçbir alan verilmedi. Değiştirilebilir alanlar: "
            f"{', '.join(CHART_FIELDS)}."
        )

    if changes.get("kind"):
        kind = str(changes["kind"])
        if kind not in SUPPORTED_KINDS:
            return f"[ERROR] kind '{kind}' desteklenmiyor. Seçenekler: {sorted(SUPPORTED_KINDS)}"

    # Column changes are validated against the file, not accepted on faith.
    # A revision that renames y to a column that does not exist would
    # otherwise store a spec that cannot render -- which breaks not just this
    # turn but every later revision, since the stored spec is the base.
    #
    # A DELETION (value None, via clear_fields) names no column, so it is not
    # validated as one -- "hue kullanma" must not be answered with "there is no
    # column called None".
    if {"x", "y", "hue", "source"} & {k for k, v in changes.items() if v is not None}:
        merged = {**obj.spec, **changes}
        for key, value in changes.items():
            if value is None:
                merged.pop(key, None)
        path = _resolve(str(merged.get("source") or ""), workspace)
        if not path.exists():
            return f"[ERROR] Veri dosyası bulunamadı: {path}"
        schema, err = describe_schema(path, str(merged.get("sheet") or ""))
        if err:
            return err
        for role in ("x", "y", "hue"):
            if changes.get(role) is None:
                continue
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
    # The object the user just edited becomes the active one. GPT review
    # (Faz 5 hazırlığı): chart_revise(object_id=B) edited B but left A active,
    # and `active(kind)` orders by is_active before updated_at -- so the very
    # next "şimdi başlığını da değiştir" went back to A, silently, while the
    # user was plainly still talking about B. "This" means the thing last
    # touched.
    if not patched.is_active:
        store.activate(conversation_id, patched.id)

    applied = ", ".join(
        (f"{k} silindi" if v is None else f"{k}={v}") for k, v in changes.items()
    )
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
        obj = store.activate(conversation_id, object_id)
        if obj is None:
            return f"[ERROR] '{object_id}' bu konuşmada bulunamadı."
        return f"[WorkingSet] Aktif nesne artık {obj.ref}.\n{obj.render()}"

    if action == "undo":
        obj = store.get(object_id) if object_id else store.active(conversation_id)
        if obj is None or obj.conversation_id != conversation_id:
            return "[ERROR] Geri alınacak nesne yok."
        if not obj.revision_history:
            return f"[WorkingSet] {obj.ref} zaten ilk halinde (v{obj.version}) — geri alınacak bir şey yok."

        if obj.kind != KIND_CHART:
            reverted, undone = store.undo(obj.id)
            if reverted is None:
                return "[ERROR] Geri alınamadı."
            return f"[WorkingSet] {reverted.ref} v{reverted.version}'e döndü (geri alınan: {undone})."

        # Render FIRST, commit second. GPT review (Faz 5 hazırlığı): this used
        # to pop the history, write the older spec, and only then try to draw
        # it -- so a render failure left the store one version back while the
        # PNG on the user's screen was still the newer one. Stored state and
        # visible artifact disagreed, and the code described that in a sentence
        # instead of preventing it. Nothing is written unless the chart drew.
        candidate, undone = store.peek_undo(obj.id)
        if candidate is None:
            return "[ERROR] Geri alınamadı."
        png = _render(candidate, workspace, plots_dir)
        if png.startswith("[ERROR]"):
            return f"{png}\n(Geri alma UYGULANMADI — grafik v{obj.version} olarak kaldı.)"

        reverted, undone = store.undo(obj.id)
        if reverted is None:
            return "[ERROR] Geri alınamadı."
        store.record_artifact(reverted.id, png)
        if not reverted.is_active:
            store.activate(conversation_id, reverted.id)
        return _describe(reverted, png, note=f"Geri alındı: {undone}")

    return f"[ERROR] Bilinmeyen action '{action}'. Geçerli: list | show | activate | undo."
