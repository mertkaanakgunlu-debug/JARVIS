"""Plot generation from tabular data (Faz 4).

Entry point:
  generate_plot(path, kind, x, y, title, hue, output, workspace_plots_dir)

Supported kinds: line, scatter, bar, hist, box, violin, heatmap
Saves PNG to workspace/data/plots/<output>.png and returns the saved path.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

SUPPORTED_KINDS = {"line", "scatter", "bar", "hist", "box", "violin", "heatmap"}

# Round 3 — inline-data guardrails. data_json comes straight out of a model's
# tool call, so a hallucinated or runaway generation could otherwise hand
# pandas/matplotlib an arbitrarily large or arbitrarily nested payload on the
# owner's machine. A chart a human will look at never legitimately needs more
# than this; anything bigger belongs in a CSV via the file path.
MAX_INLINE_BYTES = 256 * 1024
MAX_INLINE_ROWS = 10_000
MAX_INLINE_COLS = 100


def _safe_filename(s: str) -> str:
    return re.sub(r"[^\w\-.]", "_", s)


def _first_nested(values: list) -> bool:
    """True if any element is itself a container — inline data is strictly
    flat columns of primitives; nested structures are rejected, not coerced."""
    return any(isinstance(v, (dict, list)) for v in values)


def frame_from_inline(data_json: str, x: str, y: str) -> tuple["pd.DataFrame | None", str, str, str]:
    """Build a DataFrame from an inline ``data_json`` string.

    Faz 1.3 — closes the B6 router-closure gap: the ``data`` domain exposes no
    file-writing tool, so a model asked to chart numbers the user typed
    ("1, 4, 9, 16") had no way to get them onto disk for the file-only
    plot_data. Now the values ride straight into the tool.

    Accepts either a JSON object of column→values
    (``{"x": [1,2,3,4], "y": [1,4,9,16]}`` — x optional, synthesized as 0..n-1
    when absent) or a bare JSON array (``[1,4,9,16]`` — single series, x=index).

    Returns ``(df, x, y, error)``; ``error`` is "" on success, else an
    ``[ERROR] ...`` string and ``df`` is None.

    Guardrails (round 3): at most ``MAX_INLINE_BYTES`` of JSON text,
    ``MAX_INLINE_ROWS`` rows and ``MAX_INLINE_COLS`` columns; values must be
    flat primitives (no nested objects/arrays). Larger data goes through a
    CSV/Excel ``path`` instead.
    """
    if len(data_json.encode("utf-8", errors="ignore")) > MAX_INLINE_BYTES:
        return None, x, y, (
            f"[ERROR] data_json exceeds the {MAX_INLINE_BYTES // 1024} KB inline limit. "
            "Pass a CSV/Excel file via path for data this large."
        )
    try:
        parsed = json.loads(data_json)
    except Exception as exc:
        return None, x, y, f"[ERROR] data_json is not valid JSON: {exc}"

    try:
        import pandas as pd
    except ImportError:
        return None, x, y, "[ERROR] pandas is not installed. Run: pip install pandas openpyxl"

    if isinstance(parsed, list):
        if not parsed:
            return None, x, y, "[ERROR] data_json array is empty."
        if len(parsed) > MAX_INLINE_ROWS:
            return None, x, y, f"[ERROR] data_json has {len(parsed)} rows; the inline limit is {MAX_INLINE_ROWS}."
        if _first_nested(parsed):
            return None, x, y, "[ERROR] data_json array must contain only numbers/strings, not nested objects or arrays."
        df = pd.DataFrame({"y": parsed})
        df.insert(0, "x", range(len(df)))
        return df, (x or "x"), (y or "y"), ""

    if isinstance(parsed, dict):
        if not parsed:
            return None, x, y, "[ERROR] data_json object is empty."
        if len(parsed) > MAX_INLINE_COLS:
            return None, x, y, f"[ERROR] data_json has {len(parsed)} columns; the inline limit is {MAX_INLINE_COLS}."
        for k, v in parsed.items():
            if isinstance(v, dict):
                return None, x, y, f"[ERROR] data_json column {k!r} is a nested object; columns must be flat lists of numbers/strings."
            if isinstance(v, list):
                if len(v) > MAX_INLINE_ROWS:
                    return None, x, y, f"[ERROR] data_json column {k!r} has {len(v)} rows; the inline limit is {MAX_INLINE_ROWS}."
                if _first_nested(v):
                    return None, x, y, f"[ERROR] data_json column {k!r} contains nested objects/arrays; values must be flat numbers/strings."
        try:
            df = pd.DataFrame({k: (v if isinstance(v, list) else [v]) for k, v in parsed.items()})
        except Exception as exc:
            return None, x, y, f"[ERROR] Could not build data from data_json: {exc}"
        cols = list(df.columns)
        yy = y or cols[-1]
        if x:
            xx = x
        elif "x" in cols:
            xx = "x"
        elif len(cols) >= 2:
            xx = cols[0]
        else:  # single unnamed series → synthesize an index x
            df.insert(0, "x", range(len(df)))
            xx = "x"
        return df, xx, yy, ""

    return None, x, y, "[ERROR] data_json must be a JSON array or object."


def generate_plot(
    path: Path | None,
    kind: str,
    x: str,
    y: str,
    title: str,
    hue: str,
    output: str,
    plots_dir: Path,
    *,
    df: "pd.DataFrame | None" = None,
) -> str:
    """Generate a chart from a CSV/Excel file (or an inline DataFrame) and save as PNG.

    Args:
        path:      Data file (CSV or Excel). May be None when ``df`` is given.
        kind:      Plot type: line | scatter | bar | hist | box | violin | heatmap.
        x:         Column name for x-axis (not required for heatmap/hist).
        y:         Column name for y-axis (not required for heatmap/hist).
        title:     Chart title.
        hue:       Optional grouping column for colour encoding.
        output:    Output filename stem (without extension). Auto-generated if empty.
        plots_dir: Directory where the PNG will be saved.
        df:        Pre-built DataFrame (inline data path). When given, ``path``
                   is used only as a filename hint and no file is read.

    Returns:
        Absolute path to the saved PNG, or an error string starting with [ERROR].
    """
    kind = kind.lower().strip()
    if kind not in SUPPORTED_KINDS:
        return (
            f"[ERROR] Unsupported plot kind '{kind}'. "
            f"Choose from: {', '.join(sorted(SUPPORTED_KINDS))}"
        )

    # Load data — from the inline DataFrame if provided, else from the file.
    try:
        import pandas as pd  # noqa: F401  # also validates the plotting stack is installed
    except ImportError:
        return "[ERROR] pandas is not installed. Run: pip install pandas openpyxl"

    if df is None:
        if path is None or not path.exists():
            return f"[ERROR] Data file not found: {path}"
        try:
            suffix = path.suffix.lower()
            if suffix == ".csv":
                df = pd.read_csv(path, sep=None, engine="python", encoding_errors="replace")
            elif suffix in (".xlsx", ".xls"):
                df = pd.read_excel(path)
            else:
                return f"[ERROR] Unsupported file type '{suffix}'."
        except Exception as exc:
            return f"[ERROR] Could not load data: {exc}"

    # Validate columns
    if kind not in ("heatmap",) and x and x not in df.columns:
        return f"[ERROR] Column '{x}' not found. Available: {list(df.columns)}"
    if kind not in ("heatmap", "hist") and y and y not in df.columns:
        return f"[ERROR] Column '{y}' not found. Available: {list(df.columns)}"
    if hue and hue not in df.columns:
        return f"[ERROR] Hue column '{hue}' not found. Available: {list(df.columns)}"

    # Import plotting libs
    try:
        import matplotlib
        matplotlib.use("Agg")  # non-interactive backend — must be set before pyplot import
        import matplotlib.pyplot as plt
        import seaborn as sns
    except ImportError:
        return "[ERROR] matplotlib/seaborn not installed. Run: pip install matplotlib seaborn"

    sns.set_theme(style="whitegrid", palette="tab10")
    fig, ax = plt.subplots(figsize=(10, 6))

    try:
        if kind == "line":
            sns.lineplot(data=df, x=x, y=y, hue=hue or None, ax=ax)
        elif kind == "scatter":
            sns.scatterplot(data=df, x=x, y=y, hue=hue or None, ax=ax)
        elif kind == "bar":
            sns.barplot(data=df, x=x, y=y, hue=hue or None, ax=ax)
        elif kind == "hist":
            col = x or y
            if not col:
                plt.close(fig)
                return "[ERROR] hist requires x (the column to histogram)."
            sns.histplot(data=df, x=col, hue=hue or None, kde=True, ax=ax)
        elif kind == "box":
            sns.boxplot(data=df, x=x or None, y=y or None, hue=hue or None, ax=ax)
        elif kind == "violin":
            sns.violinplot(data=df, x=x or None, y=y or None, hue=hue or None, ax=ax)
        elif kind == "heatmap":
            num_df = df.select_dtypes(include="number")
            if num_df.shape[1] < 2:
                plt.close(fig)
                return "[ERROR] heatmap needs at least 2 numeric columns."
            corr = num_df.corr()
            sns.heatmap(corr, annot=True, fmt=".2f", cmap="coolwarm", ax=ax)
    except Exception as exc:
        plt.close(fig)
        return f"[ERROR] Plot generation failed: {exc}"

    if title:
        ax.set_title(title, fontsize=14, fontweight="bold")
    plt.tight_layout()

    # Save
    plots_dir.mkdir(parents=True, exist_ok=True)
    if output:
        stem = _safe_filename(output)
    else:
        base = path.stem if path is not None else "inline"
        stem = _safe_filename(f"{base}_{kind}_{x or 'data'}_{y or ''}")
        stem = stem.strip("_")
    out_path = plots_dir / f"{stem}.png"

    # Avoid overwriting — append counter if needed
    counter = 1
    candidate = out_path
    while candidate.exists():
        candidate = plots_dir / f"{stem}_{counter}.png"
        counter += 1
    out_path = candidate

    try:
        fig.savefig(out_path, dpi=150, bbox_inches="tight")
    except Exception as exc:
        plt.close(fig)
        return f"[ERROR] Could not save plot: {exc}"
    finally:
        plt.close(fig)

    return str(out_path)
