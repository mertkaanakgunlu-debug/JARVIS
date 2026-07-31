"""Postcondition verification runner -- Agent Runtime rev.2, Faz 3.

jarvis.execution.postcondition (Faz 1) defined PostconditionSpec/
PostconditionResult as shapes only -- "Faz 3 adds the runner that actually
evaluates a PostconditionSpec against an ExecutionEnvelope." This module is
that runner. Faz 8's eval_oracle.py is meant to import run_postconditions()
directly rather than re-declare an equivalent vocabulary of its own (the
plan's "Uc ilke" #2 -- one verification vocabulary, not two that can drift
apart).

Honesty discipline (postcondition.py's own docstring, restated here because
it is the one rule every kind below must obey): absence of a runnable check,
or missing information needed to run one, is reported as "unverified" --
never silently coerced to "verified". Every _check_* function below either
produces a real verified/failed verdict or explicitly returns "unverified"
with a human-readable reason; none of them have a code path that defaults to
"verified" by omission.

Params contract (deliberately simple -- this phase does not build a second
schema layer for postcondition params):
  Path-resolving kinds (file_exists, path_within_workspace, file_openable,
  artifact_hash_matches, row_count_matches, series_matches) locate their
  target via spec.params, checked in this order:
    "path"      -- a literal path string
    "path_arg"  -- a key into the tool call's OWN args dict (args[path_arg])
  Neither present, or the resolved path can't be made absolute without a
  workspace that wasn't given -> unverified.

Post-MVP Faz 1 (honesty kernel) adds the second, and deliberately still not
text-parsing, strategy this module's Agent-Runtime-era docstring left open:
"declared_artifacts_exist" reads the paths the TOOL ITSELF declared through
jarvis/execution/artifacts.py, which covers exactly the case the args lookup
structurally cannot -- plot_data's `output` being a filename STEM,
report_write deriving its path from `title`, finance('export') computing a
default. The alternative the earlier docstring called "a result-text-parsing
convention" was considered and rejected on the same grounds it was deferred
for; see the artifacts module docstring.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Callable

from jarvis.execution.postcondition import PostconditionResult, PostconditionSpec

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _target_path(spec: PostconditionSpec, workspace: Path | None, args: dict[str, Any]) -> Path | None:
    raw = spec.params.get("path")
    if not raw:
        path_arg = spec.params.get("path_arg")
        raw = args.get(path_arg) if path_arg else None
    if not raw:
        return None
    p = Path(str(raw))
    if p.is_absolute():
        return p
    if workspace is None:
        return None
    return workspace / p


def _unverified(spec: PostconditionSpec, detail: str) -> PostconditionResult:
    return PostconditionResult(spec=spec, status="unverified", detail=detail)


def _check_file_exists(spec: PostconditionSpec, workspace: Path | None, args: dict, content: str, artifacts: tuple) -> PostconditionResult:
    path = _target_path(spec, workspace, args)
    if path is None:
        return _unverified(spec, "no path available to check (missing params.path/path_arg or no workspace)")
    status = "verified" if path.exists() else "failed"
    return PostconditionResult(spec=spec, status=status, detail=str(path))


def _check_path_within_workspace(spec: PostconditionSpec, workspace: Path | None, args: dict, content: str, artifacts: tuple) -> PostconditionResult:
    if workspace is None:
        return _unverified(spec, "no workspace given to check containment against")
    path = _target_path(spec, workspace, args)
    if path is None:
        return _unverified(spec, "no path available to check")
    try:
        path.resolve().relative_to(workspace.resolve())
    except ValueError:
        return PostconditionResult(spec=spec, status="failed", detail=f"{path} escapes workspace {workspace}")
    return PostconditionResult(spec=spec, status="verified", detail=str(path))


def _check_file_openable(spec: PostconditionSpec, workspace: Path | None, args: dict, content: str, artifacts: tuple) -> PostconditionResult:
    path = _target_path(spec, workspace, args)
    if path is None:
        return _unverified(spec, "no path available to check")
    if not path.exists():
        return PostconditionResult(spec=spec, status="failed", detail=f"{path} does not exist")
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    if path.suffix.lower() in image_exts:
        try:
            from PIL import Image  # optional dependency -- not required for other kinds
            with Image.open(path) as img:
                img.verify()
            return PostconditionResult(spec=spec, status="verified", detail=str(path))
        except ImportError:
            pass  # fall through to the generic byte-read check below
        except Exception as exc:
            return PostconditionResult(spec=spec, status="failed", detail=f"unreadable image: {exc}")
    try:
        with path.open("rb") as f:
            f.read(1)
    except OSError as exc:
        return PostconditionResult(spec=spec, status="failed", detail=f"cannot open: {exc}")
    return PostconditionResult(spec=spec, status="verified", detail=str(path))


def _check_artifact_hash_matches(spec: PostconditionSpec, workspace: Path | None, args: dict, content: str, artifacts: tuple) -> PostconditionResult:
    expected = spec.params.get("expected_hash")
    if not expected:
        return _unverified(spec, "no params.expected_hash to compare against")
    path = _target_path(spec, workspace, args)
    if path is None:
        return _unverified(spec, "no path available to check")
    if not path.exists():
        return PostconditionResult(spec=spec, status="failed", detail=f"{path} does not exist")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    status = "verified" if actual.lower() == str(expected).lower() else "failed"
    return PostconditionResult(spec=spec, status=status, detail=f"actual={actual}")


def _read_rows(path: Path) -> list[list[str]] | None:
    if path.suffix.lower() in (".csv",):
        import csv
        with path.open(newline="", encoding="utf-8", errors="replace") as f:
            return list(csv.reader(f))
    if path.suffix.lower() in (".xlsx", ".xls"):
        try:
            import openpyxl
        except ImportError:
            return None
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.worksheets[0]
        return [list(row) for row in ws.iter_rows(values_only=True)]
    return None


def _check_row_count_matches(spec: PostconditionSpec, workspace: Path | None, args: dict, content: str, artifacts: tuple) -> PostconditionResult:
    expected = spec.params.get("expected_rows")
    if expected is None:
        return _unverified(spec, "no params.expected_rows to compare against")
    path = _target_path(spec, workspace, args)
    if path is None:
        return _unverified(spec, "no path available to check")
    if not path.exists():
        return PostconditionResult(spec=spec, status="failed", detail=f"{path} does not exist")
    try:
        rows = _read_rows(path)
    except Exception as exc:
        return _unverified(spec, f"could not read rows: {exc}")
    if rows is None:
        return _unverified(spec, f"no row reader for extension {path.suffix!r}")
    include_header = bool(spec.params.get("include_header", True))
    actual = len(rows) if include_header else max(0, len(rows) - 1)
    tolerance = int(spec.params.get("tolerance", 0))
    status = "verified" if abs(actual - int(expected)) <= tolerance else "failed"
    return PostconditionResult(spec=spec, status=status, detail=f"actual_rows={actual}")


def _check_series_matches(spec: PostconditionSpec, workspace: Path | None, args: dict, content: str, artifacts: tuple) -> PostconditionResult:
    """Compares a JSON manifest's declared y-series against params.expected_y.

    Deliberately reads a SIDECAR MANIFEST FILE, not the rendered image --
    recovering plotted data from pixels is out of scope. No tool in this repo
    writes such a manifest yet (plot_data's PNG output has no sidecar), so
    this kind is honestly unreachable as "verified"/"failed" today; it is
    still implemented and tested directly so the mechanism is real, and a
    future phase (a plot_data manifest, and/or a TaskContract extractor
    populating expected_y) can wire it up with zero changes needed here.
    """
    expected_y = spec.params.get("expected_y")
    if not expected_y:
        return _unverified(spec, "no params.expected_y to compare against")
    path = _target_path(spec, workspace, args)
    if path is None:
        return _unverified(spec, "no manifest path available to check")
    if not path.exists():
        return _unverified(spec, f"no manifest at {path} -- artifact may still be correct, just unverifiable")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _unverified(spec, f"manifest unreadable: {exc}")
    y_key = spec.params.get("y_key", "y")
    actual_y = manifest.get(y_key) if isinstance(manifest, dict) else None
    if actual_y is None:
        return _unverified(spec, f"manifest has no {y_key!r} key")
    tolerance = float(spec.params.get("tolerance", 1e-6))
    if len(actual_y) != len(expected_y):
        return PostconditionResult(spec=spec, status="failed", detail=f"length {len(actual_y)} != expected {len(expected_y)}")
    for a, e in zip(actual_y, expected_y):
        if abs(float(a) - float(e)) > tolerance:
            return PostconditionResult(spec=spec, status="failed", detail=f"{a} != {e} (tolerance {tolerance})")
    return PostconditionResult(spec=spec, status="verified", detail=f"{len(actual_y)} values matched")


_EXIT_CODE_RE = re.compile(r"^\[(OK|EXIT (\d+))\]", re.MULTILINE)


def _check_exit_code_matches(spec: PostconditionSpec, workspace: Path | None, args: dict, content: str, artifacts: tuple) -> PostconditionResult:
    expected = spec.params.get("expected_code")
    if expected is None:
        return _unverified(spec, "no params.expected_code to compare against")
    m = _EXIT_CODE_RE.search(content or "")
    if not m:
        return _unverified(spec, "no recognizable exit-code marker in tool result")
    actual = 0 if m.group(1) == "OK" else int(m.group(2))
    status = "verified" if actual == int(expected) else "failed"
    return PostconditionResult(spec=spec, status=status, detail=f"actual_exit_code={actual}")


def _check_record_exists(spec: PostconditionSpec, workspace: Path | None, args: dict, content: str, artifacts: tuple) -> PostconditionResult:
    db_path_raw = spec.params.get("db_path")
    table = spec.params.get("table")
    id_column = spec.params.get("id_column")
    id_arg = spec.params.get("id_arg")
    if not (db_path_raw and table and id_column and id_arg):
        return _unverified(spec, "record_exists requires params.db_path/table/id_column/id_arg")
    if not (_IDENTIFIER_RE.match(table) and _IDENTIFIER_RE.match(id_column)):
        # table/id_column are always static, ToolSpec-declared config, never
        # user/model input -- but they are string-interpolated into SQL
        # below, so this stays defense-in-depth rather than trusting that.
        return _unverified(spec, "table/id_column must be plain identifiers")
    id_value = args.get(id_arg)
    if id_value is None:
        return _unverified(spec, f"no args[{id_arg!r}] to look up")
    db_path = Path(db_path_raw)
    if not db_path.is_absolute():
        if workspace is None:
            return _unverified(spec, "relative db_path needs a workspace")
        db_path = workspace / db_path
    if not db_path.exists():
        return _unverified(spec, f"database not found: {db_path}")
    try:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                f"SELECT 1 FROM {table} WHERE {id_column} = ? LIMIT 1", (id_value,)  # noqa: S608 -- identifiers validated above
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return _unverified(spec, f"query failed: {exc}")
    status = "verified" if row is not None else "failed"
    return PostconditionResult(spec=spec, status=status, detail=f"{table}.{id_column}={id_value}")


def _check_declared_artifacts_exist(
    spec: PostconditionSpec, workspace: Path | None, args: dict, content: str, artifacts: tuple
) -> PostconditionResult:
    """Every file this call DECLARED it produced is on disk.

    Post-MVP Faz 1 (honesty kernel). The three-way outcome is the whole
    point, and each branch is deliberate:

      no declarations  -> "unverified", never "verified". A tool that
        declared nothing tells us nothing; silently passing here would hand
        every non-artifact action of a multi-action tool (finance('summary'))
        a free "independently verified" badge it did not earn -- the exact
        dishonesty postcondition.py's docstring forbids.
      all present      -> "verified".
      any missing      -> "failed", naming the missing paths. This is the
        one case that upgrades a tool's self-reported success into
        "reported successful ... but independent verification FAILED" in
        summary.py, which is what actually reaches the user.

    A declared path is used as the tool wrote it (absolute for all five of
    today's declarers); a relative one is resolved against the workspace,
    and without a workspace to resolve it, unverified rather than guessed.
    """
    if not artifacts:
        return _unverified(spec, "tool declared no artifacts for this call")
    missing: list[str] = []
    checked: list[str] = []
    for ref in artifacts:
        raw = getattr(ref, "path", "") or ""
        if not raw:
            continue
        p = Path(raw)
        if not p.is_absolute():
            if workspace is None:
                return _unverified(spec, f"relative artifact path {raw!r} needs a workspace to resolve")
            p = workspace / p
        checked.append(str(p))
        if not p.is_file():
            missing.append(str(p))
    if not checked:
        return _unverified(spec, "declared artifacts carried no usable path")
    if missing:
        return PostconditionResult(
            spec=spec, status="failed",
            detail=f"declared but not on disk: {', '.join(missing)}",
        )
    return PostconditionResult(
        spec=spec, status="verified", detail=f"{len(checked)} artifact(s) verified: {', '.join(checked)}",
    )


_RUNNERS: dict[str, Callable[[PostconditionSpec, Path | None, dict, str, tuple], PostconditionResult]] = {
    "file_exists": _check_file_exists,
    "path_within_workspace": _check_path_within_workspace,
    "file_openable": _check_file_openable,
    "artifact_hash_matches": _check_artifact_hash_matches,
    "row_count_matches": _check_row_count_matches,
    "series_matches": _check_series_matches,
    "exit_code_matches": _check_exit_code_matches,
    "record_exists": _check_record_exists,
    "declared_artifacts_exist": _check_declared_artifacts_exist,
}


def run_postconditions(
    specs: tuple[PostconditionSpec, ...],
    *,
    workspace: Path | None,
    args: dict[str, Any],
    tool_result_content: str,
    artifacts: tuple = (),
) -> list[PostconditionResult]:
    """Evaluate every declared PostconditionSpec for one completed tool call.

    Never raises -- a bug in one check must not break the turn (same
    "auxiliary observation can't take down the main path" discipline as
    audit_log.record()/tool_trace.record()). A check that itself errors is
    reported "unverified" with the exception text, not silently dropped.

    artifacts: the ArtifactRefs this call declared (Post-MVP Faz 1, see
    jarvis/execution/artifacts.py). Defaults to () so the pre-Faz-1 keyword
    call sites (workflow_engine, the existing tests) keep working unchanged;
    only "declared_artifacts_exist" reads it, and with () it reports
    unverified rather than inventing a verdict.
    """
    results: list[PostconditionResult] = []
    for spec in specs:
        fn = _RUNNERS.get(spec.kind)
        if fn is None:
            results.append(_unverified(spec, f"no runner implemented for kind={spec.kind!r}"))
            continue
        try:
            results.append(fn(spec, workspace, args or {}, tool_result_content or "", tuple(artifacts or ())))
        except Exception as exc:  # noqa: BLE001 -- see docstring
            results.append(_unverified(spec, f"postcondition runner error: {exc}"))
    return results
