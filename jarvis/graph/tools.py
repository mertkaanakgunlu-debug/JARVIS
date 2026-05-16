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
from jarvis.tools.drive import drive_control      # Faz 14
from jarvis.tools.itu_mail import itu_mail_control    # Faz 15
from jarvis.tools.finance import finance_control      # Faz 16
from jarvis.gcp_quota import (                        # Faz 17
    quota_status, quota_usage_today, quota_forecast,
)
from jarvis.tools.geo_math_tool import geo_math_control  # Faz 18
from jarvis.ws import event_bus                           # HUD show_hud signal
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
        result = latex_compile(tex_path)
        event_bus.show_hud()
        return result

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
        result = generate_plot(full, kind, x, y, title, hue, output, plots_dir)
        event_bus.show_hud()
        return result

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
        result = compose_report(title, sections_md, figures_json or "[]", reports_dir)
        event_bus.show_hud()
        return result

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

    # ── Faz 13-C: Scheduled tasks ─────────────────────────────────────────────

    @tool
    def schedule(
        action: str,
        title: str = "",
        description: str = "",
        schedule_type: str = "daily",
        run_at: str = "",
        days_of_week: str = "",
        day_of_month: int = 0,
        task_id: str = "",
    ) -> str:
        """Manage scheduled reminders and recurring tasks.

        Actions:
            add     — create a new scheduled task/reminder
            list    — list all active (and optionally done) tasks
            delete  — delete a task by id
            pause   — pause a task (skip without deleting)
            resume  — resume a paused task
            done    — list completed tasks

        Parameters for 'add':
            title:         Short title for the reminder (required)
            description:   Optional detail shown in the toast notification
            schedule_type: 'once' | 'daily' | 'weekly' | 'monthly'  (default: daily)
            run_at:        Time to fire.
                           For 'once':    ISO datetime like "2026-05-20T09:00:00"
                           For others:    HH:MM like "09:00" or "14:30"
            days_of_week:  Comma-separated weekday numbers 0–6 (Mon=0) — used for 'weekly'
                           e.g. "0,2,4" = Mon/Wed/Fri
            day_of_month:  Day number 1–31 for 'monthly'

        Parameters for delete/pause/resume:
            task_id: ID returned by 'add' or shown in 'list'

        Examples:
            schedule("add", title="Kahve molası", schedule_type="daily", run_at="14:30")
            schedule("add", title="Haftalık özet", schedule_type="weekly",
                     run_at="09:00", days_of_week="0")
            schedule("add", title="Aylık rapor", schedule_type="monthly",
                     run_at="08:00", day_of_month=1)
            schedule("add", title="Toplantı hatırlatıcı", schedule_type="once",
                     run_at="2026-05-20T14:45:00")
            schedule("list")
            schedule("delete", task_id="abc12345")
        """
        from pathlib import Path as _Path
        from jarvis.scheduler import SchedulerStore, ONCE, DAILY, WEEKLY, MONTHLY
        import json as _json

        db_path = _Path("data/sessions.db")
        store = SchedulerStore(db_path)

        action = action.strip().lower()

        if action == "add":
            if not title:
                return "⚠ title gerekli."
            if not run_at:
                return "⚠ run_at gerekli (örn. '09:00' veya '2026-05-20T09:00:00')."
            dow_list = None
            if days_of_week:
                try:
                    dow_list = [int(x.strip()) for x in days_of_week.split(",") if x.strip()]
                except ValueError:
                    return "⚠ days_of_week must be comma-separated numbers 0–6."
            dom = day_of_month or None
            try:
                task_id = store.add_task(
                    title,
                    schedule_type=schedule_type,
                    run_at=run_at,
                    description=description,
                    days_of_week=dow_list,
                    day_of_month=dom,
                )
            except ValueError as exc:
                return f"⚠ {exc}"
            # Fetch next_run for confirmation
            task = store.get_task(task_id)
            next_run = task["next_run"] if task else "?"
            type_labels = {ONCE: "tek seferlik", DAILY: "günlük",
                           WEEKLY: "haftalık", MONTHLY: "aylık"}
            label = type_labels.get(schedule_type, schedule_type)
            return (
                f"✅ Görev eklendi [{task_id}]\n"
                f"  Başlık:   {title}\n"
                f"  Tür:      {label}\n"
                f"  Saat:     {run_at}\n"
                f"  Sonraki:  {next_run}"
            )

        if action == "list":
            tasks = store.list_tasks(status="active")
            if not tasks:
                return "📋 Aktif planlı görev yok. Eklemek için: schedule('add', ...)"
            lines = [f"📋 Aktif görevler ({len(tasks)}):"]
            for t in tasks:
                lines.append(
                    f"  [{t['id']}]  {t['title']}  "
                    f"({t['schedule_type']}, {t['run_at']})  "
                    f"→ sonraki: {t['next_run'][:16]}"
                )
            return "\n".join(lines)

        if action == "done":
            tasks = store.list_tasks(status="done")
            if not tasks:
                return "Tamamlanan görev yok."
            lines = [f"✅ Tamamlananlar ({len(tasks)}):"]
            for t in tasks:
                lines.append(f"  [{t['id']}]  {t['title']}  (son çalışma: {t.get('last_run','?')[:16]})")
            return "\n".join(lines)

        if action in ("delete", "pause", "resume"):
            if not task_id:
                return f"⚠ task_id gerekli ({action} için)."
            if action == "delete":
                ok = store.delete_task(task_id)
                return f"🗑 Görev silindi: {task_id}" if ok else f"⚠ Görev bulunamadı: {task_id}"
            if action == "pause":
                ok = store.pause_task(task_id)
                return f"⏸ Görev duraklatıldı: {task_id}" if ok else f"⚠ Görev bulunamadı: {task_id}"
            if action == "resume":
                ok = store.resume_task(task_id)
                return f"▶ Görev devam ettirildi: {task_id}" if ok else f"⚠ Görev bulunamadı: {task_id}"

        return f"⚠ Bilinmeyen action: '{action}'. Geçerli: add, list, delete, pause, resume, done"

    # ── Faz 13-D: To-do list ────────────────────────────────────────────────

    @tool
    def todo(
        action: str,
        title: str = "",
        description: str = "",
        due_date: str = "",
        category: str = "other",
        todo_id: str = "",
    ) -> str:
        """Manage the personal to-do list with AI-powered prioritization.

        Actions:
            add      — add a new to-do (AI will auto-assign priority & instructions)
            list     — list all open to-dos sorted by priority
            done     — mark a to-do as completed
            delete   — delete a to-do permanently
            analyze  — re-prioritize ALL open to-dos (batch AI analysis)
            today    — show top 5 highest-priority open tasks for today
            edit     — update title / description / due_date / category

        Parameters:
            title:       Task title (required for add/edit)
            description: Optional detail or context
            due_date:    ISO date or datetime e.g. "2026-05-20" or "2026-05-20T14:00"
            category:    work | personal | research | health | finance | other
            todo_id:     ID from list/add output (required for done/delete/edit)

        Examples:
            todo("add", title="Sismik analiz raporunu bitir", due_date="2026-05-20",
                 category="research")
            todo("list")
            todo("today")
            todo("done", todo_id="a1b2c3d4")
            todo("analyze")
            todo("edit", todo_id="a1b2c3d4", due_date="2026-05-25")
        """
        from pathlib import Path as _Path
        from jarvis.todo_store import TodoStore, PRIORITY_LABELS

        db_path = _Path("data/sessions.db")
        store = TodoStore(db_path)
        action = action.strip().lower()

        if action == "add":
            if not title:
                return "⚠ title gerekli."
            tid = store.add(title, description=description, due_date=due_date, category=category)
            # Fire-and-forget async analysis
            import asyncio
            async def _bg_analyze():
                from jarvis.todo_analyzer import analyze_and_save
                await analyze_and_save(tid, title, description, settings, store)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(_bg_analyze())
                else:
                    _run_coro(_bg_analyze())
            except Exception:
                pass  # Analysis is optional
            return (
                f"✅ To-do eklendi [{tid}]\n"
                f"  Başlık: {title}\n"
                f"  Kategori: {category}\n"
                f"  Bitiş: {due_date or '—'}\n"
                f"  (Öncelik ve adımlar arka planda hesaplanıyor...)"
            )

        if action == "list":
            tasks = store.list_open()
            if not tasks:
                return "📋 Açık to-do yok. Eklemek için: todo('add', title='...')"
            lines = [f"📋 Açık görevler ({len(tasks)}):"]
            for t in tasks:
                pri = PRIORITY_LABELS.get(t.get("priority") or "", "⬜ Analiz bekleniyor")
                due = f"  📅 {t['due_date']}" if t.get("due_date") else ""
                lines.append(f"\n  [{t['id']}] {pri}\n  {t['title']}{due}")
                if t.get("instructions"):
                    first_step = t["instructions"].split("\n")[0][:80]
                    lines.append(f"  → {first_step}")
            return "\n".join(lines)

        if action == "today":
            tasks = store.top_open(n=5)
            if not tasks:
                return "🌟 Bugün için açık görev yok!"
            lines = ["🌟 Bugünün öncelikli görevleri:"]
            for i, t in enumerate(tasks, 1):
                pri = PRIORITY_LABELS.get(t.get("priority") or "", "")
                due = f" (📅 {t['due_date']})" if t.get("due_date") else ""
                lines.append(f"\n  {i}. [{t['id']}] {t['title']}{due}")
                if pri:
                    lines.append(f"     {pri}")
                if t.get("instructions"):
                    for step in t["instructions"].split("\n")[:3]:
                        if step.strip():
                            lines.append(f"     {step.strip()}")
            return "\n".join(lines)

        if action == "done":
            if not todo_id:
                return "⚠ todo_id gerekli."
            ok = store.mark_done(todo_id)
            if ok:
                task = store.get(todo_id)
                return f"✅ Tamamlandı: {task['title'] if task else todo_id}"
            return f"⚠ Bulunamadı veya zaten tamamlanmış: {todo_id}"

        if action == "delete":
            if not todo_id:
                return "⚠ todo_id gerekli."
            task = store.get(todo_id)
            title_str = task["title"] if task else todo_id
            ok = store.delete(todo_id)
            return f"🗑 Silindi: {title_str}" if ok else f"⚠ Bulunamadı: {todo_id}"

        if action == "edit":
            if not todo_id:
                return "⚠ todo_id gerekli."
            updates = {}
            if title:
                updates["title"] = title
            if description:
                updates["description"] = description
            if due_date:
                updates["due_date"] = due_date
            if category and category != "other":
                updates["category"] = category
            if not updates:
                return "⚠ Güncellenecek alan yok. title/description/due_date/category gönder."
            ok = store.update(todo_id, **updates)
            return f"✏ Güncellendi [{todo_id}]" if ok else f"⚠ Bulunamadı: {todo_id}"

        if action == "analyze":
            count = store.count_open()
            if count == 0:
                return "Açık görev yok, analiz gerekmez."
            # Run batch analysis synchronously so user gets feedback
            async def _do_analyze():
                from jarvis.todo_analyzer import reanalyze_all
                return await reanalyze_all(settings, store)
            try:
                updated = _run_coro(_do_analyze())
                return f"🧠 {updated}/{count} görev yeniden önceliklendirildi. `/todo list` ile görebilirsin."
            except Exception as exc:
                return f"⚠ Analiz hatası: {exc}"

        return f"⚠ Bilinmeyen action: '{action}'. Geçerli: add, list, today, done, delete, edit, analyze"

    # ── Faz 14: Google Drive ──────────────────────────────────────────────────

    @tool
    def google_drive(
        action: str,
        query: str = "",
        folder_id: str = "",
        file_id: str = "",
        dest_path: str = "",
        local_path: str = "",
        name: str = "",
        email: str = "",
        role: str = "reader",
        max_results: int = 20,
    ) -> str:
        """Manage Google Drive files — search, read, upload, download, share.

        Actions:
            search   — find files by keyword or Drive query syntax
            list     — list folder contents (default: root)
            read     — read file content (Google Docs/Sheets/Slides as text;
                       PDFs downloaded to data/drive_cache/ → path returned
                       for pdf_read() or pdf_vision())
            download — download any file to a local path
            upload   — upload a local file to Drive
            share    — share a file with another user
            delete   — move file to trash

        Parameters:
            query:       Search keyword or Drive API q-syntax (for search)
            folder_id:   Drive folder ID (for list/upload)
            file_id:     Drive file ID (for read/download/share/delete)
            dest_path:   Local destination path (for download)
            local_path:  Local source file path (for upload)
            name:        Filename override (for upload)
            email:       Recipient email (for share)
            role:        Permission role: reader | writer | commenter (for share)
            max_results: Max files to return (for search/list)

        PDF pipeline example:
            result = google_drive("search", query="sismik analiz")
            # → shows file_id
            path_info = google_drive("read", file_id="<id>")
            # → returns local path e.g. data/drive_cache/analiz.pdf
            content = pdf_vision(path="data/drive_cache/analiz.pdf", question="Özetle")

        Drive query syntax examples:
            "name contains 'rapor'"
            "mimeType='application/pdf' and name contains 'sismik'"
            "'folder_id' in parents"
        """
        return drive_control(
            action=action,
            query=query,
            folder_id=folder_id,
            file_id=file_id,
            dest_path=dest_path,
            local_path=local_path,
            name=name,
            email=email,
            role=role,
            max_results=max_results,
            settings=settings,
        )

    # ── Faz 15: ITU Webmail ───────────────────────────────────────────────────

    @tool
    def itu_mail(
        action: str,
        query: str = "",
        uid: str = "",
        to: str = "",
        subject: str = "",
        body: str = "",
        cc: str = "",
        reply_all: bool = False,
        max_results: int = 20,
    ) -> str:
        """Manage ITU University webmail via IMAP/SMTP.

        Actions:
            list_unread  — list unread messages in ITU inbox
            search       — search messages (FROM:x SUBJECT:x BODY:x SINCE:YYYY-MM-DD)
            read         — read full message body (uid required)
            send         — send a new email via ITU SMTP (to, subject, body required)
            reply        — reply to a message (uid + body required)
            trash        — move message to Trash (uid required)
            mark_read    — mark message as read (uid required)

        Requires ITU_USERNAME and ITU_PASSWORD in .env.
        Uses imap.itu.edu.tr:993 (SSL) and smtp.itu.edu.tr:587 (STARTTLS).

        Args:
            action:      list_unread | search | read | send | reply | trash | mark_read
            query:       IMAP search string — e.g. "SUBJECT:ödev FROM:hoca@itu.edu.tr"
            uid:         Message UID from list_unread/search (read/reply/trash/mark_read)
            to:          Recipient email address (send)
            subject:     Email subject (send)
            body:        Email body text (send/reply)
            cc:          CC recipients comma-separated (send)
            reply_all:   Reply to all recipients when True (reply)
            max_results: Max messages to return (list_unread/search, default 20)

        Examples:
            itu_mail("list_unread")
            itu_mail("search", query="SUBJECT:ödev SINCE:2026-05-01")
            itu_mail("read", uid="1234")
            itu_mail("send", to="hoca@itu.edu.tr", subject="Soru", body="Merhaba...")
            itu_mail("reply", uid="1234", body="Yanıtım...")
        """
        return itu_mail_control(
            action=action,
            query=query,
            uid=uid,
            to=to,
            subject=subject,
            body=body,
            cc=cc,
            reply_all=reply_all,
            max_results=max_results,
            settings=settings,
        )

    # ── Faz 16: Finance Analytics ─────────────────────────────────────────────

    @tool
    def finance(
        action: str,
        year: int = 0,
        month: int = 0,
        category: str = "",
        monthly_limit: float = 0.0,
        alert_threshold_pct: float = 0.8,
        months_back: int = 1,
        n: int = 5,
    ) -> str:
        """Track bank transactions and manage budgets (Burgan Bank + Gmail extraction).

        Actions:
            sync           — scan Gmail for Burgan Bank notification emails and extract
                             transactions using LLM (Gemini Flash structured output)
            summary        — monthly income, expenses, net balance + category breakdown
            recent         — list most recent transactions (default 20)
            top_categories — top N expense categories for a period
            set_budget     — define a monthly spending limit for a category
            budget_status  — show spent/limit/% for all budget categories with visual bars
            chart          — generate a Plotly HTML bar chart for a period

        Args:
            year:               Year for summary/top_categories/budget_status (default: current)
            month:              Month 1-12 (default: current)
            category:           Budget category for set_budget / filter (food | transport |
                                entertainment | bills | salary | transfer | atm | shopping |
                                health | education | other)
            monthly_limit:      Monthly spending cap in TRY (for set_budget)
            alert_threshold_pct: Fraction of limit that triggers a warning (default 0.8 = 80%)
            months_back:        How many months to scan Gmail (for sync, default 1)
            n:                  Number of results for recent/top_categories (default 5)

        Examples:
            finance("sync")                                      # pull latest Burgan mails
            finance("summary")                                   # this month's summary
            finance("summary", year=2026, month=4)              # April 2026
            finance("top_categories", n=3)
            finance("set_budget", category="food", monthly_limit=1500)
            finance("budget_status")
            finance("chart")
        """
        return finance_control(
            action=action,
            year=year,
            month=month,
            category=category,
            monthly_limit=monthly_limit,
            alert_threshold_pct=alert_threshold_pct,
            months_back=months_back,
            n=n,
            settings=settings,
        )

    # ── Faz 17: GCP Quota ────────────────────────────────────────────────────

    @tool
    def gcp_quota(action: str = "status") -> str:
        """Check Vertex AI / GCP quota usage and estimated credit remaining.

        Actions:
            status   — all metrics with Rich progress bars (RPM, credit, tokens)
            usage    — cumulative token counts + cost breakdown
            forecast — linear spend extrapolation to end of month

        Requires VERTEX_CREDIT_USD in .env for credit tracking.
        Cloud Monitoring API (google-cloud-monitoring) is optional —
        falls back to local token_tracker data if not installed/accessible.

        Args:
            action: status | usage | forecast  (default: status)
        """
        a = action.strip().lower()
        if a in ("status", ""):
            return quota_status(settings)
        if a in ("usage", "usage_today"):
            return quota_usage_today(settings)
        if a in ("forecast",):
            return quota_forecast(settings)
        return f"⚠ Bilinmeyen action: '{action}'. Geçerli: status, usage, forecast"

    # ── Faz 18: Geo-Math Sub-Agent ────────────────────────────────────────────

    @tool
    def geo_math(
        action: str,
        expression: str = "",
        variable: str = "",
        query: str = "",
        x_data: str = "[]",
        y_data: str = "[]",
        grid_data: str = "[]",
        volume_data: str = "[]",
        source_pos: str = "[5, 5]",
        velocity_grid: str = "[]",
        duration: float = 0.5,
        nz: int = 100,
        nx: int = 100,
        levels: int = 20,
        cmap: str = "RdBu_r",
        title: str = "",
        plot_type: str = "line",
    ) -> str:
        """Geophysics and advanced mathematics computation tool.

        For complex geophysical reasoning → use math_solve (delegates to GeoMathAgent).
        For actual computation → use this tool with a specific action.

        Actions:
            solve_symbolic   — SymPy symbolic solve/simplify (requires: pip install sympy)
            wolfram          — WolframAlpha step-by-step (requires WOLFRAM_APP_ID in .env)
            wave_simulate_2d — 2D acoustic FDM wave simulation (Devito or NumPy fallback)
                               → returns path to snapshot PNG
            plot_2d          — 2D line or scatter plot (seismic trace, spectrum, etc.)
                               → returns path to PNG
            plot_contour     — 2D contour map (velocity model, anomaly map, seismic section)
                               → returns path to PNG (depth-axis inverted by default)
            plot_3d_surface  — Interactive 3D surface via Plotly (requires: pip install plotly)
                               → returns path to HTML
            plot_volume      — 3D isosurface via PyVista (requires: pip install pyvista)
                               → returns path to PNG; falls back to 2D midplane slice

        Args:
            action:       One of the actions above.
            expression:   Math expression or equation for solve_symbolic (e.g. "d2u/dt2 - c**2*d2u/dx2 = 0")
            variable:     Variable to solve for (e.g. "x", "omega")
            query:        Query string for wolfram action
            x_data:       JSON array of x values for plot_2d
            y_data:       JSON array of y values for plot_2d
            grid_data:    JSON 2D array (list of lists) for plot_contour/plot_3d_surface
            volume_data:  JSON 3D array for plot_volume
            source_pos:   JSON [iz, ix] source position for wave_simulate_2d
            velocity_grid: JSON 2D array of velocities in m/s (omit for 2000 m/s homogeneous)
            duration:     Simulation time in seconds (wave_simulate_2d)
            nz:           Grid depth dimension
            nx:           Grid horizontal dimension
            levels:       Number of contour levels
            cmap:         Colormap (default 'RdBu_r' — standard for seismic)
            title:        Plot title
            plot_type:    'line' or 'scatter' for plot_2d

        Examples:
            geo_math("solve_symbolic", expression="k**2 - (omega/v)**2 = 0", variable="k")
            geo_math("wave_simulate_2d", source_pos="[10, 50]", nz=100, nx=200)
            geo_math("plot_contour", grid_data="[[...]]", title="Sismik kesit")
            geo_math("wolfram", query="dispersion relation acoustic wave equation")
        """
        # For complex geophysics reasoning, delegate to GeoMathAgent sub-agent
        if action.lower() in ("analyze", "reason", "derive", "explain"):
            from jarvis.subagents.geomath import run_geomath
            problem = expression or query or title
            if not problem:
                return "⚠ expression veya query gerekli (analyze action için)."
            return _run_coro(run_geomath(problem, settings))

        _VISUAL_GEO_ACTIONS = {"wave_simulate_2d", "plot_2d", "plot_contour", "plot_3d_surface", "plot_volume"}
        result = geo_math_control(
            action=action,
            expression=expression,
            variable=variable,
            query=query,
            x_data=x_data,
            y_data=y_data,
            grid_data=grid_data,
            volume_data=volume_data,
            source_pos=source_pos,
            velocity_grid=velocity_grid,
            duration=duration,
            nz=nz,
            nx=nx,
            levels=levels,
            cmap=cmap,
            title=title,
            plot_type=plot_type,
            settings=settings,
        )
        if action.lower() in _VISUAL_GEO_ACTIONS:
            event_bus.show_hud()
        return result

    return [
        shell_run, file_read, file_write, file_list,
        pdf_read, pdf_vision, excel_read, python_run, web_search,
        note_append, report_write, report_compile,
        math_solve, write_content, research, generate_code,
        csv_read, data_analyze, plot_data, report_compose,
        vault_search, index_doc,  # Faz 6
        url_read, deep_web_research,  # Faz 7
        spotify,  # Faz 8
        google_calendar, gmail,  # Faz 9
        schedule,                # Faz 13-C
        todo,                    # Faz 13-D
        google_drive,            # Faz 14
        itu_mail,                # Faz 15
        finance,                 # Faz 16
        gcp_quota,               # Faz 17
        geo_math,                # Faz 18
    ]
