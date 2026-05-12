"""Plot generation from tabular data (Faz 4).

Entry point:
  generate_plot(path, kind, x, y, title, hue, output, workspace_plots_dir)

Supported kinds: line, scatter, bar, hist, box, violin, heatmap
Saves PNG to workspace/data/plots/<output>.png and returns the saved path.
"""

from __future__ import annotations

import re
from pathlib import Path

SUPPORTED_KINDS = {"line", "scatter", "bar", "hist", "box", "violin", "heatmap"}


def _safe_filename(s: str) -> str:
    return re.sub(r"[^\w\-.]", "_", s)


def generate_plot(
    path: Path,
    kind: str,
    x: str,
    y: str,
    title: str,
    hue: str,
    output: str,
    plots_dir: Path,
) -> str:
    """Generate a chart from a CSV/Excel file and save as PNG.

    Args:
        path:      Data file (CSV or Excel).
        kind:      Plot type: line | scatter | bar | hist | box | violin | heatmap.
        x:         Column name for x-axis (not required for heatmap/hist).
        y:         Column name for y-axis (not required for heatmap/hist).
        title:     Chart title.
        hue:       Optional grouping column for colour encoding.
        output:    Output filename stem (without extension). Auto-generated if empty.
        plots_dir: Directory where the PNG will be saved.

    Returns:
        Absolute path to the saved PNG, or an error string starting with [ERROR].
    """
    if not path.exists():
        return f"[ERROR] Data file not found: {path}"

    kind = kind.lower().strip()
    if kind not in SUPPORTED_KINDS:
        return (
            f"[ERROR] Unsupported plot kind '{kind}'. "
            f"Choose from: {', '.join(sorted(SUPPORTED_KINDS))}"
        )

    # Load data
    try:
        import pandas as pd
    except ImportError:
        return "[ERROR] pandas is not installed. Run: pip install pandas openpyxl"

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
        stem = _safe_filename(f"{path.stem}_{kind}_{x or 'data'}_{y or ''}")
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
