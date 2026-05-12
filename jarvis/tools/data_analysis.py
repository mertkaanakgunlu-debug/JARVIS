"""Tabular data analysis helpers — CSV + Excel (Faz 4).

Two entry points:
  read_csv_file(path, rows)    — quick structural preview of a CSV
  analyze_data(path, query)    — full pandas analysis (stats, dtypes, nulls,
                                  correlations) with optional targeted query
"""

from __future__ import annotations

import io
from pathlib import Path

MAX_PREVIEW_ROWS = 100
MAX_CHARS = 10_000


# ── helpers ────────────────────────────────────────────────────────────────────

def _load_dataframe(path: Path):
    """Load a CSV or Excel file into a pandas DataFrame.

    Returns (df, error_string_or_None).
    """
    try:
        import pandas as pd
    except ImportError:
        return None, "[ERROR] pandas is not installed. Run: pip install pandas openpyxl"

    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            # Auto-detect common delimiters
            df = pd.read_csv(path, sep=None, engine="python", encoding_errors="replace")
        elif suffix in (".xlsx", ".xls"):
            df = pd.read_excel(path, engine="openpyxl" if suffix == ".xlsx" else None)
        else:
            return None, f"[ERROR] Unsupported file type '{suffix}'. Use .csv, .xlsx, or .xls."
    except Exception as exc:
        return None, f"[ERROR] Could not load file: {exc}"

    return df, None


def _truncate(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n[... truncated ...]"


# ── public API ─────────────────────────────────────────────────────────────────

def read_csv_file(path: Path, rows: int = MAX_PREVIEW_ROWS) -> str:
    """Return a structural preview of a CSV file."""
    if not path.exists():
        return f"[ERROR] File not found: {path}"

    df, err = _load_dataframe(path)
    if err:
        return err

    buf = io.StringIO()
    buf.write(f"File: {path.name}\n")
    buf.write(f"Shape: {df.shape[0]:,} rows x {df.shape[1]} columns\n")
    buf.write(f"Columns: {list(df.columns)}\n\n")

    dtypes_str = df.dtypes.to_string()
    buf.write(f"Dtypes:\n{dtypes_str}\n\n")

    preview = df.head(rows).to_string(index=False)
    buf.write(f"First {min(rows, len(df))} rows:\n{preview}\n")

    return _truncate(buf.getvalue())


def analyze_data(path: Path, query: str = "") -> str:
    """Full statistical analysis of a CSV or Excel file.

    Returns shape, dtypes, descriptive stats, missing-value report, top
    correlations, and (if ``query`` is provided) a targeted pandas summary
    derived from the query keywords.
    """
    if not path.exists():
        return f"[ERROR] File not found: {path}"

    df, err = _load_dataframe(path)
    if err:
        return err

    try:
        import pandas as pd
        import numpy as np
    except ImportError:
        return "[ERROR] pandas/numpy not installed."

    buf = io.StringIO()
    buf.write(f"=== Analysis: {path.name} ===\n")
    buf.write(f"Shape: {df.shape[0]:,} rows x {df.shape[1]} columns\n\n")

    # Dtypes
    buf.write("## Column Types\n")
    buf.write(df.dtypes.to_string())
    buf.write("\n\n")

    # Missing values
    null_counts = df.isnull().sum()
    if null_counts.any():
        buf.write("## Missing Values\n")
        missing = null_counts[null_counts > 0]
        pct = (missing / len(df) * 100).round(1)
        mv_df = pd.DataFrame({"count": missing, "pct%": pct})
        buf.write(mv_df.to_string())
        buf.write("\n\n")
    else:
        buf.write("## Missing Values: none\n\n")

    # Descriptive statistics — numeric columns
    num_cols = df.select_dtypes(include="number")
    if not num_cols.empty:
        buf.write("## Descriptive Statistics (numeric columns)\n")
        buf.write(num_cols.describe(percentiles=[0.25, 0.5, 0.75]).round(4).to_string())
        buf.write("\n\n")

    # Descriptive statistics — object/categorical columns
    cat_cols = df.select_dtypes(include=["object", "category"])
    if not cat_cols.empty:
        buf.write("## Categorical Column Summaries\n")
        for col in cat_cols.columns:
            vc = df[col].value_counts().head(10)
            buf.write(f"  {col} (top 10): {dict(vc)}\n")
        buf.write("\n")

    # Correlation matrix — top pairs
    if num_cols.shape[1] >= 2:
        corr = num_cols.corr()
        # Extract upper triangle, exclude diagonal and lower triangle
        mask = np.triu(np.ones(corr.shape, dtype=bool), k=1)
        corr_pairs = (
            corr.where(mask)
            .stack()
            .dropna()
            .reset_index()
        )
        corr_pairs.columns = ["col_a", "col_b", "correlation"]
        corr_pairs["abs"] = corr_pairs["correlation"].abs()
        top_corr = corr_pairs.sort_values("abs", ascending=False).head(10)
        buf.write("## Top Correlations\n")
        for _, row in top_corr.iterrows():
            buf.write(f"  {row['col_a']} <-> {row['col_b']}: {row['correlation']:.4f}\n")
        buf.write("\n")

    # Targeted query — simple keyword-based column filter
    if query:
        q_lower = query.lower()
        matched_cols = [
            c for c in df.columns
            if any(word in c.lower() for word in q_lower.split())
        ]
        if matched_cols:
            buf.write(f"## Query-focused columns ({matched_cols})\n")
            subset = df[matched_cols]
            if subset.select_dtypes(include="number").shape[1] > 0:
                buf.write(subset.describe().round(4).to_string())
            else:
                buf.write(subset.head(20).to_string(index=False))
            buf.write("\n\n")
        else:
            buf.write(f"## Query: '{query}'\nNo columns matched query keywords.\n\n")

    return _truncate(buf.getvalue())
