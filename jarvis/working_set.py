"""Working Set — the live objects a conversation is editing (Post-MVP Faz 4).

The problem this exists for, stated exactly. A finished turn is compacted into
history as ``[human message + short summary + final answer]``, and that rule is
**correct**: raw tool state must not accumulate in the message list. But it
means that when the user says *"çizgiyi kırmızı yap"* on the next turn, the
chart they are talking about has no representation anywhere. The model is
asked to revise something it cannot see, so it re-derives the whole thing from
one sentence — and gets the columns, the source file, or the chart type wrong.

A Working Set is the narrow exception to the compaction rule. Not the raw tool
output (that stays out, as before) but the **spec**: the structured description
that can regenerate the object. The active object's spec is injected into the
system prompt every turn, so a revision becomes a *single-argument patch*
against known state instead of a reconstruction from prose. That is precisely
the shape the measured model limit allows: it can fix one argument per turn,
it cannot hold a whole set together.

**Why not one global `current_spec`.** The user can say *"grafiği eski haline
getir ama mail taslağını değiştirme"*. That sentence is only expressible if
objects are separately addressable, so this is a keyed set with one ACTIVE
member, not a single slot.

**Where it hangs, and why that was the first decision.** Per conversation, in
SQLite, keyed by ``conversation_id`` — not on ``JarvisAgent`` and not in graph
state. One shared agent serves every client, so an agent-level attribute would
let conversation A's revision land on conversation B's chart, which is the
exact family of bug Faz 2.75 spent a package closing for pending
confirmations. Graph state was the other candidate and is genuinely tempting
(the checkpointer is already per-thread), but it is pruned and rewritten by
compaction — putting the thing that survives compaction inside the thing that
performs it. A keyed store sidesteps both, and it needs no
``ConversationRuntime`` refactor to be correct today.

Kind-agnostic on purpose. ``chart`` is the only kind with tools in this phase;
``email``/``report``/``table`` are declared destinations, and the store already
holds them because the plan's whole argument is that this primitive gets reused.
Nothing here knows what a chart is.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from jarvis.clock import local_naive_now

# Kinds the store will accept. Not enforced against a tool registry: a kind is
# a label for the prompt, and rejecting an unknown one would make adding the
# next object type a schema migration instead of a tool.
KIND_CHART = "chart"
KIND_EMAIL = "email"
KIND_REPORT = "report"
KIND_TABLE = "table"
# Post-MVP Faz 5 — the store's SECOND real consumer, and the point at which
# "kind-agnostic" stops being a claim with one example. A calendar_candidate is
# an event extracted from a mail that has NOT been created yet; it lives here
# so the user can see it, correct it and approve it across turns, exactly like
# a chart, with no new machinery.
KIND_CALENDAR_CANDIDATE = "calendar_candidate"

# How much of the working set may enter the system prompt. Post-MVP Faz 3
# measured the briefing's end-to-end latency at p50 ~23 s with essentially all
# of it in the model's own generation, so anything injected on EVERY turn has
# to be bounded by construction rather than by hoping specs stay small.
MAX_PROMPT_CHARS = 1200
MAX_VALUE_CHARS = 120

_TABLE = """
CREATE TABLE IF NOT EXISTS working_objects (
    id                    TEXT PRIMARY KEY,
    conversation_id       TEXT NOT NULL,
    kind                  TEXT NOT NULL,
    title                 TEXT,
    spec_json             TEXT NOT NULL,
    version               INTEGER NOT NULL DEFAULT 1,
    source_artifacts_json TEXT,
    history_json          TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    is_active             INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ws_conversation ON working_objects(conversation_id);
CREATE INDEX IF NOT EXISTS idx_ws_active       ON working_objects(conversation_id, is_active);
"""


def _now() -> str:
    # The configured timezone, not the OS's -- jarvis/clock.py exists because
    # those were different answers in one process.
    return local_naive_now().isoformat(timespec="seconds")


@dataclass(frozen=True)
class Revision:
    """One applied change, and enough to take it back.

    `before` holds the PREVIOUS values of exactly the keys `changes` touched.
    Storing the inverse rather than a full spec snapshot keeps history small
    and makes undo exact: re-applying `before` restores the state that existed,
    not a state reconstructed from a diff.
    """

    version: int          # the version this change PRODUCED
    changes: dict[str, Any]
    before: dict[str, Any]
    at: str

    def to_dict(self) -> dict:
        return {"version": self.version, "changes": self.changes,
                "before": self.before, "at": self.at}

    @classmethod
    def from_dict(cls, data: dict) -> "Revision":
        return cls(
            version=int(data.get("version") or 0),
            changes=dict(data.get("changes") or {}),
            before=dict(data.get("before") or {}),
            at=str(data.get("at") or ""),
        )

    def describe(self) -> str:
        return ", ".join(f"{k}={v!r}" for k, v in self.changes.items()) or "(değişiklik yok)"


@dataclass(frozen=True)
class WorkingObject:
    """One live thing the user is editing."""

    id: str
    conversation_id: str
    kind: str
    title: str
    spec: dict[str, Any]
    version: int = 1
    source_artifacts: tuple[str, ...] = ()
    created_at: str = ""
    updated_at: str = ""
    revision_history: tuple[Revision, ...] = field(default_factory=tuple)
    is_active: bool = False

    @property
    def ref(self) -> str:
        """`chart:ab12c3` — how the model names it in a tool call."""
        return f"{self.kind}:{self.id}"

    def render(self, max_value_chars: int = MAX_VALUE_CHARS) -> str:
        """The object as the model sees it in the system prompt.

        A key starting with `_` is METADATA, not a setting: it describes the
        object rather than configuring it, and it gets its own line instead of
        joining the "these are the knobs" list. `_columns` is the one that
        matters -- the source file's real column names, so a revision that
        names a column is checkable by the model BEFORE it calls anything.
        That is the plan's "schema first" rule applied where invented columns
        actually bite, which is the revision, not the first draw.
        """
        head = f"{self.ref} v{self.version}" + (f' "{self.title}"' if self.title else "")

        def clip(value) -> str:
            text = str(value)
            return text[: max_value_chars - 3] + "..." if len(text) > max_value_chars else text

        pairs, meta = [], []
        for key, value in self.spec.items():
            if value in (None, "", [], {}):
                continue
            if key.startswith("_"):
                meta.append(f"  {key.lstrip('_')}: {clip(value)}")
            else:
                pairs.append(f"{key}={clip(value)}")

        lines = [head]
        if pairs:
            lines.append("  " + " · ".join(pairs))
        lines.extend(meta)
        if self.source_artifacts:
            lines.append(f"  son çıktı: {self.source_artifacts[-1]}")
        return "\n".join(lines)


class WorkingSetStore:
    """Thread-safe SQLite store. Same shape as TodoStore/SchedulerStore."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = db_path
        self._lock = threading.Lock()
        self._conn = self._open(db_path)

    @staticmethod
    def _open(db_path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_TABLE)
        return conn

    # ── Reads ───────────────────────────────────────────────────────────────

    @staticmethod
    def _row(row: sqlite3.Row) -> WorkingObject:
        return WorkingObject(
            id=row["id"],
            conversation_id=row["conversation_id"],
            kind=row["kind"],
            title=row["title"] or "",
            spec=json.loads(row["spec_json"] or "{}"),
            version=int(row["version"] or 1),
            source_artifacts=tuple(json.loads(row["source_artifacts_json"] or "[]")),
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
            revision_history=tuple(
                Revision.from_dict(d) for d in json.loads(row["history_json"] or "[]")
            ),
            is_active=bool(row["is_active"]),
        )

    def get(self, object_id: str) -> WorkingObject | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM working_objects WHERE id=?", (object_id,)
            ).fetchone()
        return self._row(row) if row else None

    def list(self, conversation_id: str, kind: str = "") -> list[WorkingObject]:
        """Newest-updated first, so the prompt shows what the user just touched."""
        sql = "SELECT * FROM working_objects WHERE conversation_id=?"
        params: list[Any] = [conversation_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        sql += " ORDER BY updated_at DESC, created_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row(r) for r in rows]

    def active(self, conversation_id: str, kind: str = "") -> WorkingObject | None:
        """The object a bare revision applies to.

        With `kind`, the most recently updated object of that kind even if it
        is not the globally active one -- so `chart_revise` on a conversation
        whose active object is an email draft still edits the chart the user
        means, instead of refusing. Being wrong here costs a redraw; refusing
        costs the user the feature.
        """
        sql = "SELECT * FROM working_objects WHERE conversation_id=?"
        params: list[Any] = [conversation_id]
        if kind:
            sql += " AND kind=? ORDER BY is_active DESC, updated_at DESC"
            params.append(kind)
        else:
            sql += " AND is_active=1"
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return self._row(row) if row else None

    # ── Writes ──────────────────────────────────────────────────────────────

    def create(
        self,
        conversation_id: str,
        kind: str,
        spec: dict[str, Any],
        title: str = "",
        source_artifacts: tuple[str, ...] | list[str] = (),
    ) -> WorkingObject:
        """Insert a new object and make it active."""
        obj = WorkingObject(
            id=uuid.uuid4().hex[:6],
            conversation_id=conversation_id,
            kind=kind,
            title=title,
            spec=dict(spec),
            version=1,
            source_artifacts=tuple(source_artifacts),
            created_at=_now(),
            updated_at=_now(),
            is_active=True,
        )
        with self._lock:
            self._conn.execute(
                "UPDATE working_objects SET is_active=0 WHERE conversation_id=?",
                (conversation_id,),
            )
            self._conn.execute(
                """INSERT INTO working_objects
                   (id, conversation_id, kind, title, spec_json, version,
                    source_artifacts_json, history_json, created_at, updated_at, is_active)
                   VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                (
                    obj.id, obj.conversation_id, obj.kind, obj.title,
                    json.dumps(obj.spec, ensure_ascii=False), obj.version,
                    json.dumps(list(obj.source_artifacts), ensure_ascii=False),
                    "[]", obj.created_at, obj.updated_at,
                ),
            )
        return obj

    def patch(self, object_id: str, changes: dict[str, Any]) -> WorkingObject | None:
        """Apply a partial change, bump the version, record the inverse.

        A value of ``None`` REMOVES the key. That is not a convenience: some
        fields genuinely exclude each other (a chart's `hue` and `color` do,
        at the renderer), so a patch has to be able to say "this is gone" and
        not merely "this is empty". A key left at None would still render as a
        real setting in the prompt block and be re-sent to the renderer.

        Keys whose value is unchanged are dropped rather than recorded, so
        "make it red" on an already-red chart produces no revision and no
        version bump. A history of no-ops would make `undo` walk backwards
        through changes that changed nothing, which reads as undo being broken.
        """
        current = self.get(object_id)
        if current is None:
            return None

        effective = {k: v for k, v in changes.items() if current.spec.get(k) != v}
        if not effective:
            return current

        before = {k: current.spec.get(k) for k in effective}
        spec = {**current.spec, **effective}
        for key, value in effective.items():
            if value is None:
                spec.pop(key, None)
        version = current.version + 1
        history = list(current.revision_history) + [
            Revision(version=version, changes=effective, before=before, at=_now())
        ]
        return self._write(current, spec, version, history, sync_title="title" in effective)

    def undo(self, object_id: str) -> tuple[WorkingObject | None, str]:
        """Take back the last revision. Returns (object, what was undone).

        A pop, not an inverse-append: repeated undo walks backwards, which is
        what *"eski haline getir"* means. History is therefore the forward
        record of what is currently applied, and the per-revision artifacts on
        disk (each revision writes its own PNG) are the durable trail.
        """
        current = self.get(object_id)
        if current is None:
            return None, "nesne bulunamadı"
        if not current.revision_history:
            return current, ""

        last = current.revision_history[-1]
        spec = {**current.spec, **last.before}
        # Keys that did not exist before the revision are removed, not set to
        # None: a spec carrying `color=None` would render as a real setting and
        # be re-sent to the renderer as one.
        for key, value in last.before.items():
            if value is None:
                spec.pop(key, None)
        history = list(current.revision_history[:-1])
        version = max(1, current.version - 1)
        return (
            self._write(current, spec, version, history, sync_title="title" in last.before),
            last.describe(),
        )

    def peek_undo(self, object_id: str) -> tuple[dict[str, Any] | None, str]:
        """The spec undo WOULD restore, without touching stored state.

        GPT review (Faz 5 hazırlığı): undo used to pop history, write the new
        spec, and only THEN re-render -- so a render failure left the store
        rolled back while the artifact the user is looking at still showed the
        newer version. Stored state and visible output disagreed, and the code
        reported that in a string instead of preventing it. Callers now render
        this candidate FIRST and commit only if it worked.
        """
        current = self.get(object_id)
        if current is None or not current.revision_history:
            return None, ""
        last = current.revision_history[-1]
        spec = {**current.spec, **last.before}
        for key, value in last.before.items():
            if value is None:
                spec.pop(key, None)
        return spec, last.describe()

    def _write(
        self,
        current: WorkingObject,
        spec: dict[str, Any],
        version: int,
        history: list[Revision],
        sync_title: bool = False,
    ) -> WorkingObject:
        """Persist a spec change. `sync_title` keeps the `title` COLUMN equal to
        the spec's title whenever this change touched it.

        GPT review (Faz 5 hazırlığı): a chart's title lived in two places --
        `WorkingObject.title` (the column, shown in the prompt header) and
        `spec["title"]` (what the renderer draws). chart_revise patched only the
        spec, so after a title change the prompt showed the object as
        `chart:ab12c3 v2 "Eski Başlık"` on one line and `title=Yeni Başlık` on
        the next, every turn, with no way for the model to tell which was real.
        The spec is now the single source; the column follows it in the same
        write. Objects whose title never enters the spec (a create-time fallback
        like "satis / ay") keep theirs -- hence the flag rather than an
        unconditional overwrite.
        """
        title = str(spec.get("title") or "") if sync_title else current.title
        updated = replace(
            current, spec=spec, version=version, title=title,
            revision_history=tuple(history), updated_at=_now(),
        )
        with self._lock:
            self._conn.execute(
                """UPDATE working_objects
                   SET spec_json=?, version=?, history_json=?, updated_at=?, title=?
                   WHERE id=?""",
                (
                    json.dumps(spec, ensure_ascii=False), version,
                    json.dumps([r.to_dict() for r in history], ensure_ascii=False),
                    updated.updated_at, title, current.id,
                ),
            )
        return updated

    def record_artifact(self, object_id: str, path: str) -> None:
        """Append the file this object's latest version produced."""
        current = self.get(object_id)
        if current is None:
            return
        artifacts = list(current.source_artifacts) + [str(path)]
        with self._lock:
            self._conn.execute(
                "UPDATE working_objects SET source_artifacts_json=?, updated_at=? WHERE id=?",
                (json.dumps(artifacts, ensure_ascii=False), _now(), object_id),
            )

    def activate(self, conversation_id: str, object_id: str) -> WorkingObject | None:
        obj = self.get(object_id)
        if obj is None or obj.conversation_id != conversation_id:
            # Cross-conversation activation is refused rather than allowed and
            # logged. The whole reason this store is keyed is that one shared
            # agent serves every client.
            return None
        with self._lock:
            self._conn.execute(
                "UPDATE working_objects SET is_active=0 WHERE conversation_id=?",
                (conversation_id,),
            )
            self._conn.execute(
                "UPDATE working_objects SET is_active=1, updated_at=? WHERE id=?",
                (_now(), object_id),
            )
        return self.get(object_id)

    def clear(self, conversation_id: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM working_objects WHERE conversation_id=?", (conversation_id,)
            )
        return cur.rowcount or 0

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001 -- close is best-effort everywhere in this repo
            pass


_default_store: WorkingSetStore | None = None
_default_store_path: Path | None = None
_default_store_lock = threading.Lock()


def default_store() -> WorkingSetStore:
    """The process store, alongside sessions/todos/entities.

    GPT review (Faz 5 hazırlığı): this was a constructor in disguise. Despite
    the name it returned a NEW store -- a new SQLite connection, a new WAL
    setup -- on every call, and every `plot_data` / `chart_revise` /
    `working_set` invocation called it once and never closed the result, so
    connections accumulated until GC happened to collect them. Memoized now, so
    the name is true and the agent's long-lived store and the tools' store are
    the same object.

    Keyed on the resolved path: tests move JARVIS_HOME between cases (see
    tests/conftest.py's isolated_cwd), and a cache that ignored that would hand
    a test the previous test's database.
    """
    global _default_store, _default_store_path
    from jarvis import paths

    path = paths.data_dir() / "sessions.db"
    with _default_store_lock:
        if _default_store is None or _default_store_path != path:
            if _default_store is not None:
                _default_store.close()
            _default_store = WorkingSetStore(path)
            _default_store_path = path
        return _default_store


def reset_default_store() -> None:
    """Drop the memoized store (tests, and JARVIS_HOME switches)."""
    global _default_store, _default_store_path
    with _default_store_lock:
        if _default_store is not None:
            _default_store.close()
        _default_store = None
        _default_store_path = None


# ── Prompt injection ─────────────────────────────────────────────────────────


def render_block(
    objects: list[WorkingObject],
    max_chars: int = MAX_PROMPT_CHARS,
) -> str:
    """The system-prompt block, bounded by construction.

    Appended to the prompt (like the environment block) rather than placed in
    the retrieved-context section: this is state THIS SYSTEM wrote, not
    something recalled from a vector store, and the untrusted-reference framing
    that correctly guards memory would be wrong here.

    Returns "" when there is nothing to say, so the common case -- most turns,
    most conversations -- costs zero tokens rather than a "(none)" placeholder
    on every request.
    """
    if not objects:
        return ""

    active = next((o for o in objects if o.is_active), None) or objects[0]
    others = [o for o in objects if o.id != active.id]

    lines = [
        "\n\n## Üzerinde çalışılan nesneler (working set)",
        "Bunlar bu konuşmada ürettiğin, hâlâ düzenlenebilir nesneler. "
        "Kullanıcı bir değişiklik istediğinde SIFIRDAN üretme — aşağıdaki "
        "spec'i temel al ve YALNIZCA değişen alanı gönder.",
        "",
        "AKTİF → " + active.render(),
    ]
    for obj in others:
        lines.append("        " + obj.render().replace("\n", "\n        "))

    lines.append("")
    lines.append(
        "Revizyon kuralları: `chart_revise` YALNIZCA değişen argümanı alır; "
        "göndermediğin her alan olduğu gibi korunur. Bir öncekine dönmek için "
        "`working_set('undo')`. Başka bir nesneye geçmek için "
        "`working_set('activate', object_id='...')`. Kullanıcı bir değişiklik "
        "İSTEMEDİYSE bu araçları çağırma."
    )

    block = "\n".join(lines)
    if len(block) <= max_chars:
        return block

    # Over budget: keep the active object whole and drop the others, naming
    # how many were dropped. Silent truncation would make "why did it forget
    # my other chart" unanswerable from the prompt -- the same reasoning
    # tool_router logs its dropped tools for.
    trimmed = [line for line in lines if not line.startswith("        ")]
    if others:
        trimmed.insert(4, f"        (+{len(others)} nesne daha, yer kalmadığı için gösterilmedi)")
    block = "\n".join(trimmed)
    return block[:max_chars]


def block_for_conversation(
    conversation_id: str,
    store: WorkingSetStore | None = None,
    max_chars: int = MAX_PROMPT_CHARS,
) -> str:
    """One call for the agent: conversation id → prompt block (or "")."""
    if not conversation_id:
        return ""
    # No close(): default_store() is memoized now, so the store handed back here
    # is the process-wide one every tool call shares -- closing it would pull the
    # connection out from under them.
    store = store or default_store()
    try:
        return render_block(store.list(conversation_id), max_chars=max_chars)
    except Exception:  # noqa: BLE001 -- a prompt block must never kill a turn
        return ""
