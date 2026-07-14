"""ToolSpec registry — per-tool risk metadata.

Phase 2: read-only; nothing gates on these specs yet.
Phase 3 (confirmation gate) will use risk_level and requires_confirmation
to insert a LangGraph interrupt before L3 tool calls.

Risk levels
-----------
L0  answer-only      — no tool call
L1  read / analysis  — no writes, no external side-effects
L2  reversible write — local files / DB, local compute, reversible external
                       (e.g. Spotify play — can be undone with pause)
L3  external effect  — email send, calendar create/delete, Drive upload/delete,
                       shell command execution
L4  destructive      — irreversible delete / format (none currently;
                       covered at L3 by shell DENY_PATTERNS)

Side-effect types
-----------------
none            — purely computational, no I/O beyond in-memory state
local_read      — reads local filesystem or local DB
local_write     — writes local filesystem or local DB
local_execute   — spawns a subprocess or script
external_read   — reads from an external network service (web, API, IMAP read)
external_write  — creates / updates / sends via external service
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    category: str           # filesystem | compute | network | memory | external_api | ui | sub_agent
    risk_level: int         # 0–4  (see module docstring)
    requires_confirmation: bool
    side_effect_type: str   # see module docstring
    timeout_seconds: int = 60
    supports_background: bool = False
    description: str = ""   # one-line, used in docs/TOOLS.md


TOOL_SPECS: dict[str, "ToolSpec"] = {s.name: s for s in [
    # ── Filesystem ──────────────────────────────────────────────────────────────
    ToolSpec(
        "shell_run", "compute", 3, True, "local_execute",
        timeout_seconds=120, supports_background=True,
        description="Run a PowerShell command (deny-list blocks destructive ops)",
    ),
    ToolSpec(
        "file_read", "filesystem", 1, False, "local_read",
        timeout_seconds=30,
        description="Read a text file from the workspace",
    ),
    ToolSpec(
        "file_write", "filesystem", 2, False, "local_write",
        timeout_seconds=30,
        description="Write or overwrite a file (creates parent dirs)",
    ),
    ToolSpec(
        "file_list", "filesystem", 1, False, "local_read",
        timeout_seconds=10,
        description="List files and directories at a path",
    ),
    ToolSpec(
        "pdf_read", "filesystem", 1, False, "local_read",
        timeout_seconds=60, supports_background=True,
        description="Extract text from a PDF (marker-pdf + pdfplumber fallback)",
    ),
    ToolSpec(
        "pdf_vision", "network", 1, False, "external_read",
        timeout_seconds=60, supports_background=True,
        description="Analyse a PDF or image visually via Gemini Vision",
    ),
    ToolSpec(
        "excel_read", "filesystem", 1, False, "local_read",
        timeout_seconds=30,
        description="Read an Excel file — column list + first 50 rows per sheet",
    ),
    ToolSpec(
        "csv_read", "filesystem", 1, False, "local_read",
        timeout_seconds=10,
        description="Preview a CSV file (shape + first N rows)",
    ),

    # ── Compute / data ───────────────────────────────────────────────────────────
    ToolSpec(
        "python_run", "compute", 2, False, "local_execute",
        timeout_seconds=120, supports_background=True,
        description="Execute a Python script in a subprocess",
    ),
    ToolSpec(
        "data_analyze", "compute", 1, False, "local_read",
        timeout_seconds=60, supports_background=True,
        description="Full pandas statistical analysis of a tabular file",
    ),
    ToolSpec(
        "plot_data", "compute", 2, False, "local_write",
        timeout_seconds=60, supports_background=True,
        description="Generate a matplotlib/seaborn PNG and save to workspace",
    ),
    ToolSpec(
        "report_write", "filesystem", 2, False, "local_write",
        timeout_seconds=30,
        description="Write a LaTeX .tex source file to vault/reports/",
    ),
    ToolSpec(
        "report_compile", "compute", 2, False, "local_execute",
        timeout_seconds=120, supports_background=True,
        description="Compile a .tex file to PDF via pdflatex",
    ),
    ToolSpec(
        "report_compose", "compute", 2, False, "local_write",
        timeout_seconds=120, supports_background=True,
        description="Write markdown + figures into a LaTeX report PDF",
    ),

    # ── Vault / notes ────────────────────────────────────────────────────────────
    ToolSpec(
        "note_append", "filesystem", 2, False, "local_write",
        timeout_seconds=10,
        description="Append markdown text to a vault note file",
    ),
    ToolSpec(
        "vault_search", "memory", 1, False, "local_read",
        timeout_seconds=15,
        description="Semantic RAG search over indexed vault documents",
    ),
    ToolSpec(
        "index_doc", "memory", 2, False, "local_write",
        timeout_seconds=60, supports_background=True,
        description="Index a document into the jarvis_docs ChromaDB collection",
    ),

    # ── Network / research ───────────────────────────────────────────────────────
    ToolSpec(
        "web_search", "network", 1, False, "external_read",
        timeout_seconds=30,
        description="Quick Tavily web search returning snippets",
    ),
    ToolSpec(
        "url_read", "network", 1, False, "external_read",
        timeout_seconds=30,
        description="Fetch a URL and return clean article text",
    ),
    ToolSpec(
        "deep_web_research", "network", 1, False, "external_read",
        timeout_seconds=120, supports_background=True,
        description="Multi-step Tavily search + fetch + Gemini Pro synthesis with citations",
    ),

    # ── Sub-agents ───────────────────────────────────────────────────────────────
    ToolSpec(
        "math_solve", "sub_agent", 2, False, "none",
        timeout_seconds=120, supports_background=True,
        description="MathAgent: symbolic + numerical math (SymPy / Gemini)",
    ),
    ToolSpec(
        "write_content", "sub_agent", 2, False, "none",
        timeout_seconds=120, supports_background=True,
        description="WriterAgent: long-form text generation",
    ),
    ToolSpec(
        "research", "sub_agent", 1, False, "external_read",
        timeout_seconds=120, supports_background=True,
        description="ResearchAgent: multi-source web research with citations",
    ),
    ToolSpec(
        "generate_code", "sub_agent", 2, False, "none",
        timeout_seconds=120, supports_background=True,
        description="CoderAgent: code generation and explanation",
    ),
    ToolSpec(
        "geo_math", "sub_agent", 2, False, "local_write",
        timeout_seconds=180, supports_background=True,
        description="GeoMathAgent: SymPy + Devito FDM + Plotly/PyVista visualisation",
    ),

    # ── External APIs — reversible (L2) ──────────────────────────────────────────
    ToolSpec(
        "spotify", "external_api", 2, False, "external_write",
        timeout_seconds=15,
        description="Play/pause/resume/next/previous/current via Spotify Web API",
    ),
    ToolSpec(
        "hud_panels", "ui", 2, False, "none",
        timeout_seconds=5,
        description="Show/hide/toggle panels in the Electron HUD",
    ),
    ToolSpec(
        "schedule", "compute", 2, False, "local_write",
        timeout_seconds=10,
        description="Scheduled tasks and reminders stored in SQLite",
    ),
    ToolSpec(
        "todo", "compute", 2, False, "local_write",
        timeout_seconds=30,
        description="To-do list with LLM priority analysis, stored in SQLite",
    ),
    ToolSpec(
        "finance", "external_api", 2, False, "external_read",
        timeout_seconds=60, supports_background=True,
        description="Burgan Bank finance sync (Gmail read-only), summary, budget tracking",
    ),
    ToolSpec(
        "gcp_quota", "network", 1, False, "external_read",
        timeout_seconds=30,
        description="GCP Vertex AI quota status and usage tracking",
    ),

    # ── External APIs — side-effect (L3) ─────────────────────────────────────────
    ToolSpec(
        "google_calendar", "external_api", 3, True, "external_write",
        timeout_seconds=30,
        description="Google Calendar: list/search (L1 actions) + create/update/delete (L3 actions)",
    ),
    ToolSpec(
        "gmail", "external_api", 3, True, "external_write",
        timeout_seconds=30,
        description="Gmail: list/read/search (L1 actions) + send/reply/trash/mark_read (L3 actions)",
    ),
    ToolSpec(
        "google_drive", "external_api", 3, True, "external_write",
        timeout_seconds=60, supports_background=True,
        description="Google Drive: search/read/download (L1 actions) + upload/share/delete (L3 actions)",
    ),
    ToolSpec(
        "itu_mail", "external_api", 3, True, "external_write",
        timeout_seconds=30,
        description="ITU webmail IMAP/SMTP: list/read/search (L1 actions) + send/reply/trash (L3 actions)",
    ),

    # ── Faz 2: cognitive memory ────────────────────────────────────────────────
    ToolSpec(
        "procedure_save", "memory", 2, False, "local_write",
        timeout_seconds=15,
        description="Save a reusable multi-step workflow to procedural memory",
    ),
]}


def get_spec(tool_name: str) -> ToolSpec | None:
    """Return the ToolSpec for *tool_name*, or None if not registered."""
    return TOOL_SPECS.get(tool_name)


# Convenience views ──────────────────────────────────────────────────────────────

def tools_at_risk(level: int) -> list[ToolSpec]:
    """Return specs whose risk_level equals *level*."""
    return [s for s in TOOL_SPECS.values() if s.risk_level == level]


def tools_requiring_confirmation() -> list[ToolSpec]:
    """Return specs where requires_confirmation is True."""
    return [s for s in TOOL_SPECS.values() if s.requires_confirmation]
