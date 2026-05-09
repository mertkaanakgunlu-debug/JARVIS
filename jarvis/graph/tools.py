"""LangChain @tool wrappers around existing JARVIS tool functions.

Workspace, settings, and memory are captured via the make_tools() factory so
LLM schemas stay clean (no injected dependency parameters visible to the model).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import asyncio
import concurrent.futures

from langchain_core.tools import tool

from jarvis.tools import files as file_tools
from jarvis.tools import shell as shell_tools
from jarvis.tools.notes import append_note
from jarvis.tools.pdf import read_pdf
from jarvis.tools.pdf_vision import read_pdf_vision
from jarvis.tools.web import tavily_search
from jarvis.tools.latex import latex_write, latex_compile
from jarvis.tools.excel import read_excel
from jarvis.tools import python_exec
from jarvis.subagents.math import run_math
from jarvis.subagents.writer import run_writer
from jarvis.subagents.research import run_research
from jarvis.subagents.coder import run_coder

if TYPE_CHECKING:
    from jarvis.config import Settings
    from jarvis.memory import Memory


def _run_coro(coro):
    """Run a coroutine from sync context even inside a running event loop."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def make_tools(workspace: Path, settings: "Settings", memory: "Memory") -> list:
    """Build LangChain tool instances capturing workspace/settings/memory in closures."""

    @tool
    def shell_run(command: str) -> str:
        """Run a PowerShell command (opening apps, checking state, running scripts — no destructive ops)."""
        safe, reason = shell_tools.is_safe(command)
        if not safe:
            return f"[BLOCKED] {reason}. Please ask the user to run this manually."
        return shell_tools.run(command)

    @tool
    def file_read(path: str) -> str:
        """Read a text file. Accepts workspace-relative or absolute paths within home dir."""
        try:
            return file_tools.read(path, workspace)
        except (FileNotFoundError, PermissionError) as e:
            return f"[ERROR] {e}"

    @tool
    def file_write(path: str, content: str) -> str:
        """Write text to a file (creates parent dirs). Path relative to workspace."""
        try:
            return file_tools.write(path, content, workspace)
        except PermissionError as e:
            return f"[ERROR] {e}"

    @tool
    def file_list(path: str = ".") -> str:
        """List files and directories at path (default: workspace root)."""
        try:
            return file_tools.list_dir(path, workspace)
        except (FileNotFoundError, NotADirectoryError, PermissionError) as e:
            return f"[ERROR] {e}"

    @tool
    def pdf_read(path: str) -> str:
        """Convert a PDF to markdown and return its content.

        Uses marker-pdf (ML-based, structure-preserving) when installed — handles text,
        tables, equations. Results are cached as .md for instant re-reads.
        Falls back to pdfplumber (text-only) if marker-pdf is not installed.
        For image-heavy PDFs (maps, seismic sections) use pdf_vision instead.
        """
        full_path = workspace / path if not Path(path).is_absolute() else Path(path)
        cache_dir = workspace / "data" / "pdf_cache"
        return read_pdf(full_path, cache_dir)

    @tool
    def pdf_vision(path: str, question: str, pages: str = "") -> str:
        """Use Gemini Vision to analyze visual content in a PDF.

        Unlike pdf_read (text extraction), this sends the PDF directly to Gemini's
        visual AI which can interpret charts, maps, seismic cross-sections, contour
        maps, and any image-heavy content.

        Args:
            path:     Path to the PDF (workspace-relative or absolute).
            question: What to analyze or describe in the PDF.
            pages:    Optional page subset to send — "1", "2-4", "1,3,5" (1-indexed).
                      Leave empty to send the entire PDF. Use for large files.
        """
        full_path = workspace / path if not Path(path).is_absolute() else Path(path)
        return read_pdf_vision(full_path, question, settings, pages or None)

    @tool
    def excel_read(path: str, sheet: str | None = None) -> str:
        """Read an Excel file (.xlsx/.xls) — returns column list + first 50 rows per sheet."""
        full = workspace / path if not Path(path).is_absolute() else Path(path)
        return read_excel(full, sheet)

    @tool
    def python_run(script_path: str) -> str:
        """Execute a Python script (use after generate_code produces a plot/analysis script)."""
        full = workspace / script_path if not Path(script_path).is_absolute() else Path(script_path)
        return python_exec.run_script(full)

    @tool
    def web_search(query: str) -> str:
        """Quick web search via Tavily. For deep multi-step research use the research tool."""
        return tavily_search(query, api_key=settings.tavily_api_key)

    @tool
    def note_append(topic: str, body: str) -> str:
        """Save a markdown note to the vault when the user asks to remember something."""
        return append_note(topic, body, memory)

    @tool
    def report_write(title: str, body: str) -> str:
        """Write a LaTeX report to vault/reports/{title}.tex. Call report_compile after."""
        reports_dir = workspace / "vault" / "reports"
        return latex_write(title, body, reports_dir)

    @tool
    def report_compile(tex_path: str) -> str:
        """Compile a .tex file to PDF via pdflatex. On failure, fix the LaTeX and retry."""
        return latex_compile(tex_path)

    @tool
    def math_solve(problem: str) -> str:
        """Delegate to MathAgent for algebra, calculus, ODEs, linear algebra, stats. Returns LaTeX."""
        return _run_coro(run_math(problem, settings))

    @tool
    def write_content(topic: str, style: str) -> str:
        """Delegate to WriterAgent for academic prose (abstracts, intros, conclusions). Returns LaTeX."""
        return _run_coro(run_writer(topic, style, settings))

    @tool
    def research(query: str) -> str:
        """Delegate to ResearchAgent for web-augmented research with citations. Returns LaTeX."""
        return _run_coro(run_research(query, settings))

    @tool
    def generate_code(spec: str) -> str:
        """Delegate to CoderAgent for Python/scripts/algorithms. Returns LaTeX with code blocks."""
        return _run_coro(run_coder(spec, settings))

    return [
        shell_run, file_read, file_write, file_list,
        pdf_read, pdf_vision, excel_read, python_run, web_search,
        note_append, report_write, report_compile,
        math_solve, write_content, research, generate_code,
    ]
