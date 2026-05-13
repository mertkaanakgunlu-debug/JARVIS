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
from jarvis.tools.latex import latex_write, latex_compile, compose_report
from jarvis.tools.excel import read_excel
from jarvis.tools import python_exec
from jarvis.tools.data_analysis import read_csv_file, analyze_data
from jarvis.tools.plotting import generate_plot
from jarvis.tools.indexer import index_file
from jarvis.tools.webfetch import fetch_url
from jarvis.tools.deep_research import run_deep_research
from jarvis.tools.spotify import spotify_control
from jarvis.tools.calendar import calendar_control
from jarvis.tools.gmail import gmail_control
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

    # ── Faz 4: Data Analysis + Plotting + Report Compose ──────────────────────

    @tool
    def csv_read(path: str, rows: int = 100) -> str:
        """Read a CSV file — returns shape, column types, and the first N rows.

        Args:
            path: Path to the CSV file (workspace-relative or absolute).
            rows: Number of preview rows to include (default 100).
        """
        full = workspace / path if not Path(path).is_absolute() else Path(path)
        return read_csv_file(full, rows)

    @tool
    def data_analyze(path: str, query: str = "") -> str:
        """Full statistical analysis of a CSV or Excel file.

        Returns: shape, dtypes, descriptive stats, missing-value report,
        top pairwise correlations (numeric columns), and categorical summaries.
        Pass an optional query to highlight columns matching that keyword
        (e.g. query="depth" focuses stats on depth-related columns).

        Args:
            path:  Path to CSV or Excel file (workspace-relative or absolute).
            query: Optional keyword to filter column-level stats.
        """
        full = workspace / path if not Path(path).is_absolute() else Path(path)
        return analyze_data(full, query)

    @tool
    def plot_data(
        path: str,
        kind: str,
        x: str = "",
        y: str = "",
        title: str = "",
        hue: str = "",
        output: str = "",
    ) -> str:
        """Generate a chart from a CSV or Excel file and save as PNG.

        Supported kinds: line, scatter, bar, hist, box, violin, heatmap.
        - heatmap: auto-uses correlation matrix, no x/y needed.
        - hist:    only x (the column to histogram) needed.
        - box/violin: x = grouping column (optional), y = value column.

        Args:
            path:   Data file path (workspace-relative or absolute).
            kind:   Chart type (line | scatter | bar | hist | box | violin | heatmap).
            x:      Column for x-axis (or histogram column for hist).
            y:      Column for y-axis.
            title:  Chart title text.
            hue:    Optional column for colour grouping.
            output: Output filename stem (auto-generated if empty).

        Returns:
            Absolute path to the saved PNG file.
        """
        full = workspace / path if not Path(path).is_absolute() else Path(path)
        plots_dir = workspace / "data" / "plots"
        return generate_plot(full, kind, x, y, title, hue, output, plots_dir)

    @tool
    def report_compose(title: str, sections_md: str, figures_json: str = "") -> str:
        """Build a structured LaTeX report from markdown sections + embedded figures.

        Converts markdown (## headings, ### subheadings, plain paragraphs) to LaTeX
        sections and embeds PNG figures. Call report_compile on the returned .tex path
        to produce a PDF.

        Args:
            title:       Report title.
            sections_md: Markdown body — use ## for sections, ### for subsections.
            figures_json: JSON array of figure dicts (optional):
                          [{"path": "/abs/path/to/plot.png", "caption": "Figure caption"}]

        Returns:
            Path to the written .tex file.
        """
        reports_dir = workspace / "vault" / "reports"
        return compose_report(title, sections_md, figures_json or "[]", reports_dir)

    # ── Faz 6: Document RAG ───────────────────────────────────────────────────

    @tool
    def vault_search(query: str, n: int = 5) -> str:
        """Semantic search over indexed documents in the JARVIS vault (RAG).

        Use this when the user asks questions about previously indexed files,
        reports, or notes. Returns the most relevant passages and their sources.
        If no documents are indexed yet, prompt the user to run index_doc first.

        Args:
            query: Natural-language search query or key terms.
            n:     Number of result passages to return (default 5, max 20).
        """
        hits = memory.search_vault(query, min(n, 20))
        if not hits:
            indexed = memory.list_indexed()
            if not indexed:
                return "[vault_search] No documents indexed yet. Ask the user to index files with index_doc first."
            names = ", ".join(Path(p).name for p in indexed)
            return f"[vault_search] No relevant passages found. Indexed files: {names}"
        lines = [f"[vault_search] Top {len(hits)} passages:"]
        for i, h in enumerate(hits, 1):
            src = Path(h["source"]).name
            score = h["score"]
            lines.append(f"\n[{i}] {src} (relevance {score:.2f})\n{h['content'][:600]}")
        return "\n".join(lines)

    @tool
    def index_doc(path: str) -> str:
        """Index a document into the JARVIS vault for semantic search (RAG).

        Reads the file, splits it into overlapping text chunks, and stores them
        in ChromaDB so vault_search can find relevant passages. Re-indexing a file
        replaces its previous index entry.

        Supported formats: .pdf, .md, .txt, .tex, .py, .json, .csv

        Args:
            path: Path to the file (workspace-relative or absolute).
        """
        full = workspace / path if not Path(path).is_absolute() else Path(path)
        return index_file(full, memory)

    # ── Faz 7: Deep Web Research ──────────────────────────────────────────────

    @tool
    def url_read(url: str) -> str:
        """Fetch a URL and return its full readable text content.

        Useful for reading specific articles, documentation pages, or any web URL
        the user wants JARVIS to analyse. Uses Firecrawl if configured, otherwise
        trafilatura (free, no API key needed). Returns up to ~8000 characters.

        Args:
            url: Full URL including http:// or https://.
        """
        return fetch_url(url, settings)

    @tool
    def deep_web_research(topic: str, max_sources: int = 5) -> str:
        """Deep multi-step research: search the web, read sources, synthesize with citations.

        Unlike web_search (quick snippets), this tool:
        1. Searches Tavily for relevant URLs
        2. Fetches and reads the full content of each page
        3. Synthesizes a structured markdown report with [N] citations
        4. Includes a References section

        Use for: in-depth technical questions, literature summaries, current-events
        reports, comparative analyses. Slower than web_search (15-30s typical).

        Args:
            topic:       Research topic or question (natural language).
            max_sources: Number of sources to fetch and synthesize (default 5, max 10).
        """
        n = min(max(1, max_sources), 10)
        return run_deep_research(topic, settings, n)

    # ── Faz 8: Spotify ────────────────────────────────────────────────────────

    @tool
    def spotify(action: str, query: str = "") -> str:
        """Control Spotify music playback.

        Actions:
          play <query>  — search and immediately play a track (e.g., "play Bohemian Rhapsody")
          pause         — pause current playback
          resume        — resume paused playback
          next          — skip to the next track
          previous      — go back to the previous track
          current       — show what's currently playing

        Requires SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET in .env.
        First call opens a browser for one-time OAuth login.

        Args:
            action: One of play | pause | resume | next | previous | current.
            query:  Search terms (only needed for 'play').
        """
        return spotify_control(action, query, settings)

    # ── Faz 9: Google Calendar ────────────────────────────────────────────────

    @tool
    def google_calendar(
        action: str,
        title: str = "",
        date: str = "",
        time: str = "",
        duration_minutes: int = 60,
        description: str = "",
        location: str = "",
        days_ahead: int = 7,
        query: str = "",
        event_id: str = "",
    ) -> str:
        """Manage Google Calendar events.

        Actions:
          list   — show upcoming events (days_ahead window, default 7)
          create — create a new event (title + date required; time optional for all-day)
          delete — delete by event_id, or by query (finds first match)
          search — search future events by keyword
          update — update event fields (event_id required)

        Requires GOOGLE_CALENDAR_CREDS_FILE in .env pointing to OAuth credentials JSON.
        First call opens a browser for one-time consent; token cached at data/.calendar_token.json.

        Args:
            action:           list | create | delete | search | update
            title:            Event title (create/update)
            date:             Date — YYYY-MM-DD, DD/MM/YYYY, 'today', 'tomorrow' (create/update)
            time:             Start time HH:MM in 24h (create/update; omit for all-day event)
            duration_minutes: Duration in minutes (default 60)
            description:      Optional event description
            location:         Optional event location
            days_ahead:       How many days ahead to look (list action, default 7)
            query:            Keyword for search/delete actions
            event_id:         Calendar event ID for delete/update (use search to find it)
        """
        return calendar_control(
            action=action,
            title=title,
            date=date,
            time=time,
            duration_minutes=duration_minutes,
            description=description,
            location=location,
            days_ahead=days_ahead,
            query=query,
            event_id=event_id,
            settings=settings,
        )

    # ── Faz 9: Gmail ──────────────────────────────────────────────────────────

    @tool
    def gmail(
        action: str,
        query: str = "",
        message_id: str = "",
        to: str = "",
        subject: str = "",
        body: str = "",
        max_results: int = 10,
    ) -> str:
        """Manage Gmail — read, send, reply, search, and organise emails.

        Actions:
          list_unread  — list unread emails in inbox (max_results, default 10)
          search       — search with Gmail query syntax (e.g. "from:boss subject:report")
          read         — read full email content (message_id required)
          send         — send a new email (to, subject, body required)
          reply        — reply to an email (message_id + body required)
          trash        — move email to trash (message_id required)
          mark_read    — mark email as read (message_id required)

        Uses the same OAuth credentials as Google Calendar.
        First call opens a browser for Gmail consent; token cached at data/.gmail_token.json.

        Args:
            action:      list_unread | search | read | send | reply | trash | mark_read
            query:       Gmail search query (search action)
            message_id:  Email ID from list_unread/search (read/reply/trash/mark_read)
            to:          Recipient email address (send)
            subject:     Email subject (send)
            body:        Email body text (send/reply)
            max_results: Max emails to return (list_unread/search, default 10)
        """
        return gmail_control(
            action=action,
            query=query,
            message_id=message_id,
            to=to,
            subject=subject,
            body=body,
            max_results=max_results,
            settings=settings,
        )

    return [
        shell_run, file_read, file_write, file_list,
        pdf_read, pdf_vision, excel_read, python_run, web_search,
        note_append, report_write, report_compile,
        math_solve, write_content, research, generate_code,
        csv_read, data_analyze, plot_data, report_compose,
        vault_search, index_doc,  # Faz 6
        url_read, deep_web_research,  # Faz 7
        spotify,  # Faz 8
        google_calendar,  # Faz 9
        gmail,            # Faz 9
    ]
