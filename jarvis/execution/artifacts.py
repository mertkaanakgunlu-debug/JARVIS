"""Declared artifacts -- Post-MVP Faz 1 (honesty kernel), plan item 4.

The problem this closes, in the plan's own words: *"artifact bildirimi
standartlasir -- path_from_result string-parse'i yerine."* Today only
file_write declares a postcondition (tool_registry.py), because it is the
only artifact-producing tool whose output path IS a direct call argument.
plot_data's `output` is a filename STEM, report_write derives its path from
`title`, and finance('export') returns a whole formatted summary block --
so for every one of them the question "did the file you just claimed to
write actually appear on disk?" was structurally unanswerable, and their
envelopes could only ever say "reported successful by the tool (not
independently verified)".

The obvious fix -- regex the path back out of the tool's human-readable
return string -- is exactly what the plan rejects, and for a good reason
that was verified before this module was written: those strings are prose
written for a human and a model ("Compiled successfully: C:\\...",
"Grafik olusturuldu: ...", a six-line Turkish cash-flow summary), they
differ per tool and per language, and a parser over them is a second,
undeclared contract that drifts silently the first time someone rewords a
message.

So the tool declares instead. A tool that writes a file calls declare()
with the real, final path at the moment it actually has it -- inside
generate_plot right after fig.savefig(), inside export_cashflow_workbook
right after wb.save(). The declaration rides back to the graph out of band
on the LangChain ToolMessage.artifact field (an existing, first-class
channel for exactly this: structured data from a tool that is NOT meant for
the model to read), so:

  * the model-facing return string is untouched -- byte for byte. That is
    deliberate and load-bearing: MEMORY/ROADMAP both record that editing a
    tool's text moves measured behavior as much as editing its code (one
    added docstring sentence took a gate from 10/10 to 0/10 with zero tool
    calls). A verification layer must not pay for itself in behavior drift.
  * nothing needs to parse anything.

Transport: a ContextVar holding a per-call list, opened by
jarvis/graph/safe_tools.py's wrap hooks around each tool invocation. Two
concurrency properties this relies on, both real rather than assumed:
asyncio.gather gives every tool call its own context copy (so two parallel
calls cannot see each other's sink), and a sync tool dispatched into a
worker thread runs under copy_context(), which shares the same list OBJECT
-- appends made in the thread are visible to the awaiting parent. Both are
covered by tests/test_declared_artifacts.py rather than trusted.

declare() with no active sink is a deliberate no-op, never an error: the
same helpers are called directly by the CLI, by scripts/ and by unit tests
that never go through a ToolNode, and a verification aid must not be able
to break the thing it observes.
"""
from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

# Closed vocabulary, same discipline as PostconditionSpec.kind -- a future
# reader should not have to reverse-engineer what an artifact "is" from the
# strings that happen to appear in the wild.
ArtifactKind = Literal[
    "chart",          # plot_data / finance('export')'s embedded chart -- PNG
    "workbook",       # finance('export') -- .xlsx
    "report_source",  # report_write / report_compose -- .tex
    "report_pdf",     # report_compile -- .pdf
    "document",       # any other written document
    "data",           # exported data file (csv/json)
]


class ArtifactRef(BaseModel):
    """One file a tool says it produced. `path` is whatever the tool actually
    wrote -- absolute where the tool has an absolute path (all five of
    today's declarers do), never re-derived or "corrected" here."""
    path: str
    kind: ArtifactKind = "document"
    produced_by: str = ""


_SINK: contextvars.ContextVar[list[ArtifactRef] | None] = contextvars.ContextVar(
    "jarvis_declared_artifacts", default=None
)


@contextmanager
def collecting() -> Iterator[list[ArtifactRef]]:
    """Open a per-tool-call declaration sink. Strictly nested (set/reset via
    token), so sequential calls in one task isolate correctly too, not just
    the gather()-parallel case."""
    sink: list[ArtifactRef] = []
    token = _SINK.set(sink)
    try:
        yield sink
    finally:
        _SINK.reset(token)


def declare(path: str | Path, *, kind: ArtifactKind = "document", produced_by: str = "") -> None:
    """Record that THIS tool call produced the file at `path`.

    Call it at the point the file is genuinely on disk (after save/write),
    not where the path is computed -- the whole value of the declaration is
    that a later file_exists check either confirms it or contradicts the
    tool, and a path declared before the write would turn a real failure
    into a confirmed success.
    """
    sink = _SINK.get()
    if sink is None:
        return  # not inside a tool call -- see module docstring
    text = str(path).strip()
    if not text:
        return
    sink.append(ArtifactRef(path=text, kind=kind, produced_by=produced_by))


def declared() -> tuple[ArtifactRef, ...]:
    """Everything declared so far in the current call (test/introspection aid)."""
    sink = _SINK.get()
    return tuple(sink) if sink else ()


def parse_refs(raw: object) -> tuple[ArtifactRef, ...]:
    """Rebuild ArtifactRefs from a ToolMessage.artifact payload.

    Tolerant by design and never raises: the payload survives a round trip
    through the LangGraph SqliteSaver (so it comes back as plain dicts), an
    old checkpoint predates this field entirely, and a tool using LangChain's
    own response_format="content_and_artifact" could have put something else
    there. Anything unrecognized is dropped, which degrades verification to
    "unverified" -- never to a false "verified".
    """
    if not isinstance(raw, (list, tuple)):
        return ()
    out: list[ArtifactRef] = []
    for item in raw:
        if isinstance(item, ArtifactRef):
            out.append(item)
            continue
        if isinstance(item, dict):
            try:
                out.append(ArtifactRef(**item))
            except Exception:  # noqa: BLE001 -- see docstring
                continue
    return tuple(out)
