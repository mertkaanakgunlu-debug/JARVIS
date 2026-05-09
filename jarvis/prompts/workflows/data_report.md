## Data-analysis + report workflow

When the task involves data files and a final PDF report:
1. `pdf_read` the assignment; `excel_read` the data file (absolute Desktop paths are fine).
2. Delegate plot scripting to `generate_code`. Instruct it to write a standalone `plot.py` that reads the Excel with pandas, generates matplotlib plots, and saves PNGs using relative paths (e.g. `plot1.png`).
3. `file_write` the script to `vault/reports/<task>/plot.py`, then `python_run("vault/reports/<task>/plot.py")`.
4. `file_list("vault/reports/<task>")` to confirm PNGs exist.
5. Build the LaTeX body — reference plots with `\includegraphics{plot1.png}`. Then `report_write` + `report_compile`.
6. If `report_compile` returns a log error, read it, fix the LaTeX, re-write + re-compile.
