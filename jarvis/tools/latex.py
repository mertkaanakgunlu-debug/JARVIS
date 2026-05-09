"""LaTeX report writing and compilation tools."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


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
    return str(tex_path)


def latex_compile(tex_path: str | Path) -> str:
    """Compile a .tex file to PDF using pdflatex.

    Runs in a temp directory to avoid polluting the source dir with .aux/.log files.
    On success returns the path to the produced .pdf.
    On failure returns the last 60 lines of the .log file so the LLM can self-correct.
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
        for _ in range(2):
            proc = subprocess.run(
                [pdflatex, "-interaction=nonstopmode", "-halt-on-error", tmp_tex.name],
                cwd=tmp_path,
                capture_output=True,
                text=True,
                timeout=60,
            )

        pdf_tmp = tmp_tex.with_suffix(".pdf")
        if pdf_tmp.exists():
            dest = tex_path.with_suffix(".pdf")
            shutil.copy(pdf_tmp, dest)
            return f"Compiled successfully: {dest}"

        # Compilation failed — return last 60 log lines
        log_file = tmp_tex.with_suffix(".log")
        log_tail = ""
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = "\n".join(lines[-60:])
        return f"[COMPILE ERROR] pdflatex failed.\n\nLast log lines:\n{log_tail}"


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
