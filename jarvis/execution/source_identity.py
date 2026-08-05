"""Which data source did the user actually name? -- one shared identity.

Post-MVP: Completion Contract Source Binding. The pilot of 2026-08-05 found
the contract's worst failure mode: asked for a chart of a file that did not
exist, a completion repair listed the workspace, found a DIFFERENT fixture,
drew a chart from it, and the contract recorded `SATISFIED` -- because
success only ever checked "was a chart artifact registered", never "was it
drawn from what the user asked for". Closing that gap needs the SAME source
identity in three places that must never drift apart from each other:

    jarvis/nlu/output_intent.py         -- what did the user's OWN words name?
    jarvis/execution/output_contract.py -- what did the tool CALL actually use?
    the pre-execution guard (nodes.py)  -- block a mismatched call before it runs

Three independent normalizations of "is satis.csv the same file as
./satis.csv" is exactly the kind of thing that quietly disagrees the first
time one of them is edited and the others are not. This module is the one
place that decision lives.

A source reference is a small, JSON-serializable dict -- not a class -- so it
survives the checkpointer unchanged, the same convention `required_outputs`
already uses:

    {"type": "file", "raw": "satis.csv", "basename": "satis.csv"}
    {"type": "file", "raw": "satis.csv", "basename": "satis.csv",
     "path": "c:\\...\\workspace\\satis.csv"}   # only when derivable
    {"type": "inline"}                          # plot_data(data_json=...)
    {"type": "unknown"}                         # nothing usable to compare

`path` is populated only when it can be derived WITHOUT inventing information:
either the raw text was already an absolute reference (a pure string check --
no filesystem access) or a `workspace` was given to resolve a relative one
against. Never fabricated to force a match.

Nothing here reads a file's contents or opens a store. `Path.resolve()`
canonicalizes a path STRING (case, separators, `..` segments); it does not
require the file to exist (`strict=False`) and is the same discipline
`jarvis.execution.output_contract.canonical()` already applies to artifact
paths. That is what keeps this safe to call from output_contract.classify(),
which is documented PURE.
"""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path
from typing import Any

#: Same vocabulary and order as jarvis/nlu/output_intent.py's _DATA_FILE --
#: kept as a literal tuple rather than importing it, so this module stays
#: dependency-free of nlu/ (see the module docstring: three consumers, one
#: identity, but source_identity itself must not become a fourth thing NLU
#: depends on transitively).
_TABULAR_EXT = ("csv", "xlsx", "xlsm", "xls", "tsv", "json", "parquet")

#: Punctuation a filename token never legitimately contains in this corpus.
#: Deliberately a NEGATED class (not `[\w\-. ]+`): a permissive class that
#: allows a plain space would let a greedy match swallow the Turkish words
#: BEFORE the filename too ("Masaüstündeki satis.csv" as one token) --
#: harmless where output_intent.py's own _DATA_FILE only checks presence,
#: fatal here where the matched TEXT is the thing being extracted. Excluding
#: whitespace and quote/bracket punctuation keeps the match to one
#: contiguous token -- which is how a path is actually written, spaces and
#: all, when one is typed at all.
_EXCLUDED = "\\s\"'\u201c\u201d\u2018\u2019\u00ab\u00bb\u201e,;()\\[\\]{}"
_FILE_REF_RE = re.compile(
    r"\b[^" + _EXCLUDED + r"]+?\.(?:" + "|".join(_TABULAR_EXT) + r")\b",
    re.IGNORECASE,
)

_QUOTE_CHARS = "\"'\u201c\u201d\u2018\u2019\u00ab\u00bb\u201e"


def _fold(text: str) -> str:
    """Unicode- and case-insensitive comparison key (NFKC then casefold)."""
    return unicodedata.normalize("NFKC", text).strip().casefold()


def _strip_quotes(text: str) -> str:
    return text.strip().strip(_QUOTE_CHARS).strip()


def _looks_absolute(raw: str) -> bool:
    """Pure string check, no I/O. `Path.is_absolute()` alone misses a
    drive-relative Windows form (`Path("C:satis.csv").is_absolute()` is
    False), so a plain drive-letter/UNC prefix is also accepted -- still
    string-only, never a filesystem call."""
    if not raw:
        return False
    if Path(raw).is_absolute():
        return True
    return bool(re.match(r"^[A-Za-z]:[\\/]", raw)) or raw.startswith("\\\\")


def extract_source_ref(query: object) -> dict | None:
    """The explicit file the user named in their own words, or None.

    None means "no explicit source" -- callers must NOT invent one for a
    generic reference ("bu dosya", "the data", "the table"); a made-up source
    would be worse than none, since it would make an unbound request LOOK
    source-bound and reject a perfectly good chart later.

    No filesystem access, no state, no LLM -- this runs at the entry point,
    before any tool call exists to compare against, the same discipline
    jarvis/nlu/output_intent.py itself follows.
    """
    if not isinstance(query, str) or not query.strip():
        return None
    match = _FILE_REF_RE.search(query)
    if not match:
        return None
    ref = normalize_source_ref(_strip_quotes(match.group(0)))
    if ref["type"] != "file" or not ref["basename"]:
        return None
    return ref


def normalize_source_ref(value: Any, workspace: Any = None) -> dict:
    """A raw string, or a partial source-ref dict, -> the canonical shape.

    Idempotent: normalizing an already-normalized ref returns the same
    identity, so a caller that already has a canonical dict can pass it
    straight through without a branch to skip this call.

    `is_explicit_path` records whether `raw` named a DIRECTORY at all (`./
    satis.csv`, `Desktop/satis.csv`, an absolute path) as opposed to a bare
    filename (`satis.csv`) -- source_matches() uses this, not merely
    whether a `path` happens to be resolvable, to decide between full-path
    and basename-only comparison. This distinction is load-bearing: a live
    smoke run caught the bug its absence causes -- a user asking for a bare
    "satis.csv" had their own correct file rejected, because the model
    (reasonably) called plot_data(path="Desktop/satis.csv") and a naive
    "both sides resolved a path, so compare paths" rule made two spellings
    of the SAME request disagree with each other.

    `path` is set only when derivable without guessing: the raw text was
    already absolute (pure string check), or `workspace` was given to
    resolve a relative one against. Absent `workspace` and a relative `raw`,
    the dict simply has no `path` key.
    """
    if isinstance(value, dict):
        vtype = str(value.get("type") or "").strip().lower() or "file"
        raw = str(value.get("raw") or value.get("path") or "")
    else:
        vtype = "file"
        raw = str(value or "")
    raw = _strip_quotes(raw)

    if vtype == "inline":
        return {"type": "inline", "raw": raw, "basename": ""}
    if not raw:
        return {"type": "unknown", "raw": "", "basename": ""}

    slash_form = raw.replace("\\", "/").rstrip("/")
    basename = _fold(slash_form.rsplit("/", 1)[-1]) if slash_form else ""
    is_explicit_path = "/" in slash_form or _looks_absolute(raw)
    out: dict[str, Any] = {
        "type": "file", "raw": raw, "basename": basename,
        "is_explicit_path": is_explicit_path,
    }

    path = ""
    if workspace is not None:
        try:
            base = Path(workspace)
            candidate = Path(raw)
            candidate = candidate if candidate.is_absolute() else (base / raw)
            path = os.path.normcase(os.path.normpath(str(candidate.resolve(strict=False))))
        except (OSError, ValueError):  # pragma: no cover -- malformed path
            path = ""
    elif _looks_absolute(raw):
        # An explicit absolute reference is preserved even with no workspace
        # to resolve against -- nothing needs guessing to canonicalize it.
        path = os.path.normcase(os.path.normpath(raw))
    if path:
        out["path"] = path
    return out


def source_matches(required: dict | None, actual: dict | None, workspace: Any = None) -> bool:
    """Does `actual` (what a tool call, or a working-set object, used) name
    the same source as `required` (what the user asked for)?

    False on any ambiguity -- never a silent match. Matching rules, in order:

      * either side missing, or either side not type "file" (inline vs an
        explicit file request, or a side with nothing usable) -> False.
      * different basenames -> False, unconditionally
        (`olmayan.csv` vs `satis.csv`).
      * the REQUEST named only a bare filename (`required["is_explicit_path"]`
        is False) -> basename identity IS the match, whatever directory the
        call's own path resolves to. This is "yalnız basename verilmişse
        basename karşılaştırılmalı" -- keyed on the REQUEST's specificity,
        not on whether a `path` happens to be resolvable on either side. A
        live smoke run is why this is keyed this way and not the other:
        asked for a bare "satis.csv", a model calling
        plot_data(path="Desktop/satis.csv") is answering correctly, and an
        earlier version of this function rejected its own right answer by
        comparing full paths whenever a workspace happened to be available.
      * the REQUEST named an explicit path (`./satis.csv`, an absolute path,
        `Desktop/satis.csv`) -> full canonical path equality decides it, so
        `satis.csv`, `./satis.csv` and its absolute form all agree when they
        resolve to the same file, and a same-named file in a DIFFERENT
        directory correctly does not. Unresolvable on either side (no
        workspace, or a malformed candidate) -> False; a request specific
        enough to name a path is never satisfied by a same-basename guess.

    A caller with nothing to compare (an unbound requirement -- no "source"
    key at all) must not call this in the first place; that is a different,
    earlier decision than what this function makes.
    """
    if not required or not actual:
        return False
    req = normalize_source_ref(required, workspace)
    act = normalize_source_ref(actual, workspace)
    if req["type"] != "file" or act["type"] != "file":
        return False
    if not req["basename"] or not act["basename"]:
        return False
    if req["basename"] != act["basename"]:
        return False
    if not req.get("is_explicit_path"):
        return True
    req_path, act_path = req.get("path", ""), act.get("path", "")
    return bool(req_path and act_path and req_path == act_path)


def safe_source_label(value: Any) -> str:
    """A display/telemetry label that never carries an absolute local path
    (and therefore never a username) -- basename only, original casing
    preserved, or a fixed word for inline/unresolvable data.
    """
    ref = value if isinstance(value, dict) and "type" in value else normalize_source_ref(value)
    if ref.get("type") == "inline":
        return "inline data"
    raw = str(ref.get("raw") or "")
    if not raw:
        return "unknown source"
    display = raw.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return display or "unknown source"
