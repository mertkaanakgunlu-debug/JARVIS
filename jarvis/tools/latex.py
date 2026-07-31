"""LaTeX report writing and compilation tools."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from jarvis.execution.artifacts import declare as declare_artifact


def latex_write(title: str, body: str, reports_dir: Path) -> str:
    """Write a LaTeX document to vault/reports/{title}.tex.

    `body` should be valid LaTeX content for the document body
    (everything that goes between \\begin{document} and \\end{document}).
    Returns the path to the written .tex file.
    """
    reports_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    safe = safe.replace(" ", "_")
    tex_path = reports_dir / f"{safe}.tex"

    # Minimal preamble that handles math + UTF-8 + hyperlinks
    document = rf"""\documentclass{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage{{amsmath, amssymb, amsthm}}
\usepackage{{geometry}}
\usepackage{{hyperref}}
\geometry{{a4paper, margin=2.5cm}}
\title{{{_escape_latex(title)}}}
\date{{\today}}
\begin{{document}}
\maketitle
{body}
\end{{document}}
"""
    tex_path.write_text(document, encoding="utf-8")
    # Post-MVP Faz 1 -- the path is derived from `title`, never passed in, so
    # it is undiscoverable from the call's arguments. See jarvis/execution/artifacts.py.
    declare_artifact(tex_path, kind="report_source", produced_by="report_write")
    return str(tex_path)


def latex_compile(tex_path: str | Path, *, timeout: float = 120.0) -> str:
    """Compile a .tex file to PDF using pdflatex.

    Runs in a temp directory to avoid polluting the source dir with .aux/.log files.
    On success returns the path to the produced .pdf.
    On failure returns the last 60 lines of the .log file so the LLM can self-correct.

    Agent Runtime rev.2, Faz 3: timeout (matching ToolSpec.timeout_seconds,
    passed explicitly by jarvis/graph/tools.py's report_compile wrapper) is
    split across the two pdflatex passes below rather than applied to each in
    full -- the whole compile operation should be bounded by timeout total,
    not up to 2x that. subprocess.TimeoutExpired is deliberately NOT caught
    here; it propagates to safe_tools.py's shared exception boundary, same as
    shell.py's run()/python_exec.py's run_script().
    """
    pdflatex = shutil.which("pdflatex")
    if not pdflatex:
        # MiKTeX installs to a versioned path; try common locations
        candidates = [
            r"C:\Program Files\MiKTeX\miktex\bin\x64\pdflatex.exe",
            r"C:\Users\mertk\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.exe",
        ]
        for c in candidates:
            if Path(c).exists():
                pdflatex = c
                break
        if not pdflatex:
            return (
                "[ERROR] pdflatex not found on PATH. "
                "Open MiKTeX Console → Settings → enable 'Register MiKTeX in system PATH', "
                "then restart JARVIS."
            )

    tex_path = Path(tex_path)
    if not tex_path.exists():
        return f"[ERROR] .tex file not found: {tex_path}"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        # Copy source to temp dir
        tmp_tex = tmp_path / tex_path.name
        shutil.copy(tex_path, tmp_tex)

        # Run pdflatex twice (needed for TOC/references to resolve)
        per_pass_timeout = timeout / 2
        for _ in range(2):
            proc = subprocess.run(
                [pdflatex, "-interaction=nonstopmode", "-halt-on-error", tmp_tex.name],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=per_pass_timeout,
            )

        pdf_tmp = tmp_tex.with_suffix(".pdf")
        if pdf_tmp.exists():
            dest = tex_path.with_suffix(".pdf")
            shutil.copy(pdf_tmp, dest)
            # Post-MVP Faz 1 -- declared after the copy out of the temp dir, so
            # what is verified is the PDF the user can actually open, not the
            # one that briefly existed in a directory this function deletes.
            declare_artifact(dest, kind="report_pdf", produced_by="report_compile")
            return f"Compiled successfully: {dest}"

        # Compilation failed — return last 60 log lines
        log_file = tmp_tex.with_suffix(".log")
        log_tail = ""
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = "\n".join(lines[-60:])
        return f"[COMPILE ERROR] pdflatex failed.\n\nLast log lines:\n{log_tail}"


def compose_report(
    title: str,
    sections_md: str,
    figures_json: str,
    reports_dir: Path,
) -> str:
    """Build a structured LaTeX report from markdown sections and embedded figures.

    Args:
        title:       Report title.
        sections_md: Markdown body — use ## headers for sections, ### for subsections.
                     Regular paragraphs become LaTeX paragraphs.
        figures_json: JSON array of figure dicts:
                      [{"path": "/abs/or/rel/file.png", "caption": "..."}]
                      Figures are appended in order at the bottom of each section
                      if their caption matches a section header keyword, otherwise
                      placed at the end of the document.
        reports_dir: Directory where the .tex file is written.

    Returns:
        Path to the written .tex file (caller can pass to latex_compile).
    """
    # Parse figures
    figures: list[dict] = []
    if figures_json.strip():
        try:
            figures = json.loads(figures_json)
            if not isinstance(figures, list):
                figures = []
        except json.JSONDecodeError:
            figures = []

    body_lines: list[str] = []

    # Convert markdown sections to LaTeX
    for raw_line in sections_md.splitlines():
        line = raw_line.rstrip()
        if line.startswith("### "):
            heading = _escape_latex(line[4:].strip())
            body_lines.append(rf"\subsubsection{{{heading}}}")
        elif line.startswith("## "):
            heading = _escape_latex(line[3:].strip())
            body_lines.append(rf"\subsection{{{heading}}}")
        elif line.startswith("# "):
            heading = _escape_latex(line[2:].strip())
            body_lines.append(rf"\section{{{heading}}}")
        elif line.startswith("**") and line.endswith("**"):
            body_lines.append(rf"\textbf{{{_escape_latex(line[2:-2])}}}")
        elif line == "":
            body_lines.append("")
        else:
            body_lines.append(_escape_latex(line))

    # Append figures
    for fig in figures:
        fig_path = str(fig.get("path", "")).replace("\\", "/")
        caption = _escape_latex(str(fig.get("caption", "")))
        body_lines.append(r"\begin{figure}[h!]")
        body_lines.append(r"  \centering")
        body_lines.append(rf"  \includegraphics[width=0.9\linewidth]{{{fig_path}}}")
        if caption:
            body_lines.append(rf"  \caption{{{caption}}}")
        body_lines.append(r"\end{figure}")
        body_lines.append("")

    # Assemble preamble — add graphicx for figures
    reports_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip()
    safe = safe.replace(" ", "_")
    tex_path = reports_dir / f"{safe}.tex"

    document = rf"""\documentclass{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage{{amsmath, amssymb, amsthm}}
\usepackage{{geometry}}
\usepackage{{hyperref}}
\usepackage{{graphicx}}
\geometry{{a4paper, margin=2.5cm}}
\title{{{_escape_latex(title)}}}
\date{{\today}}
\begin{{document}}
\maketitle
{chr(10).join(body_lines)}
\end{{document}}
"""
    tex_path.write_text(document, encoding="utf-8")
    declare_artifact(tex_path, kind="report_source", produced_by="report_compose")
    return str(tex_path)


def _escape_latex(text: str) -> str:
    """Escape special LaTeX characters in plain-text strings."""
    replacements = [
        ("\\", r"\textbackslash{}"),
        ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
        ("#", r"\#"), ("_", r"\_"), ("{", r"\{"),
        ("}", r"\}"), ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ]
    for old, new in replacements:
        text = text.replace(old, new)
    return text
