"""Excel reader — returns a compact text summary suitable for LLM context.

Automatically detects the header row (the first row whose values are all strings,
not numbers) and reports both the raw top rows AND the cleaned DataFrame so the
LLM can write the correct ``pd.read_excel(skiprows=..., header=...)`` call.
"""

from __future__ import annotations

from pathlib import Path

MAX_ROWS_PREVIEW = 50
MAX_CHARS = 10000


def _find_header_row(df_raw, max_scan: int = 6) -> int:
    """Return the 0-based index of the most likely header row.

    Strategy: among the first ``max_scan`` rows scan for the row that has the
    largest number of non-null string values (typical column-name rows have all
    columns labeled, while title / merged-cell rows have only a few non-null).
    Ties are broken by preferring the earlier row.
    """
    best_row = 0
    best_count = -1
    for i, row in df_raw.head(max_scan).iterrows():
        non_null = row.dropna()
        str_count = sum(isinstance(v, str) for v in non_null)
        if str_count > best_count:
            best_count = str_count
            best_row = int(i)
    return best_row


def read_excel(path: Path, sheet: str | int | None = None) -> str:
    """Read an .xlsx/.xls file and return a text summary of its contents."""
    if not path.exists():
        return f"[ERROR] File not found: {path}"
    try:
        import pandas as pd
    except ImportError:
        return "[ERROR] pandas is not installed. Run: pip install pandas openpyxl"

    try:
        xl = pd.ExcelFile(path)
    except Exception as exc:
        return f"[ERROR] Could not open Excel file: {exc}"

    sheets = [sheet] if sheet is not None else xl.sheet_names
    out: list[str] = []
    for s in sheets:
        try:
            # First: read raw (no header parsing) to detect structure
            df_raw = xl.parse(s, header=None)

            # Find the header row
            header_row = _find_header_row(df_raw)

            # Build the list of rows to skip (everything before the header, minus the units row)
            # Also skip any row immediately after the header that has no string values (units row)
            rows_to_skip = list(range(header_row))
            # Check if the row after the header is a units row (all non-null values are strings too)
            if header_row + 1 < len(df_raw):
                next_row = df_raw.iloc[header_row + 1].dropna()
                is_units_row = all(isinstance(v, str) for v in next_row) if len(next_row) > 0 else False
                if is_units_row:
                    rows_to_skip.append(header_row + 1)

            # Now parse cleanly
            df = xl.parse(s, skiprows=rows_to_skip, header=0)

            head = df.head(MAX_ROWS_PREVIEW).to_string(index=False)

            # Build skiprows param string for the LLM
            if rows_to_skip:
                skip_hint = f"skiprows={rows_to_skip}, header=0"
            else:
                skip_hint = "header=0  (no rows to skip)"

            out.append(
                f"## Sheet: {s}\n"
                f"Header detected at Excel row {header_row} (0-based).\n"
                f"Recommended pandas read: pd.read_excel(path, {skip_hint})\n"
                f"Shape: {df.shape[0]} rows × {df.shape[1]} cols\n"
                f"Columns: {list(df.columns)}\n\n"
                f"First {min(MAX_ROWS_PREVIEW, len(df))} rows:\n{head}"
            )
        except Exception as exc:
            out.append(f"## Sheet: {s}\n[ERROR reading sheet: {exc}]")
            continue

    text = "\n\n".join(out)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + "\n\n[... truncated ...]"
    return text
