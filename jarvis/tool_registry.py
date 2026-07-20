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

Categories
----------
filesystem | compute | network | memory | external_api | ui | sub_agent
mcp   — Faz 5: tools discovered at runtime from an external MCP server
        (e.g. Playwright browser automation). See jarvis/mcp_integration.py
        and register_dynamic_spec() below -- these specs are NOT in the
        TOOL_SPECS literal below (the tool names don't exist until the
        server actually responds), they're inserted at connect time.
"""
from __future__ import annotations

from dataclasses import dataclass, replace


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
    domain: str = ""        # Sprint 2 (Faz 2A) capability-router grouping —
                            # assigned via _TOOL_DOMAINS below, NOT per-ctor;
                            # "" is treated as "mcp" (quarantine) by the router.
                            # category answers "what kind of thing is this?"
                            # (risk/audit axis); domain answers "which user
                            # intents should SEE it?" — external_api alone
                            # lumps gmail+calendar+drive+spotify together,
                            # which is exactly what a scoped subset can't do.


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
        # BUG-1 (Faz 4): was L2/no-confirm -- arbitrary, unsandboxed Python
        # execution from any absolute path is strictly more powerful than
        # shell_run (deny-listed L3), so it cannot sit at a lower gate than
        # shell_run. Reclassified to match; true sandboxing (resource/network
        # restrictions on the subprocess itself) is not implemented and
        # stays a deferred hardening item -- this fix is the access gate,
        # not a sandbox.
        "python_run", "compute", 3, True, "local_execute",
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


# ── Sprint 2 (Faz 2A): capability-router domain map ──────────────────────────
# One place, not 36 ctor edits. Every STATIC tool must appear here — the
# import-time check below fails loudly on a new tool that forgot to pick a
# domain (silently landing in the "mcp" quarantine would just make the tool
# invisible to routing, a confusing way to discover the omission). Dynamic
# MCP specs are deliberately NOT here: domain="" ⇒ router treats as "mcp".
_TOOL_DOMAINS: dict[str, str] = {
    # files — local documents & filesystem
    "file_read": "files", "file_write": "files", "file_list": "files",
    "pdf_read": "files", "pdf_vision": "files", "excel_read": "files",
    "csv_read": "files",
    # web — outbound reads
    "web_search": "web", "url_read": "web", "deep_web_research": "web",
    "research": "web",
    # mail / calendar / drive — split on purpose; see ToolSpec.domain comment
    "gmail": "mail", "itu_mail": "mail",
    "google_calendar": "calendar",
    "google_drive": "drive",
    # data — analysis, math, plotting, reports, content generation
    "data_analyze": "data", "plot_data": "data", "report_write": "data",
    "report_compile": "data", "report_compose": "data", "math_solve": "data",
    "geo_math": "data", "write_content": "data",
    # system — execution & machine control
    "shell_run": "system", "python_run": "system", "generate_code": "system",
    "gcp_quota": "system", "hud_panels": "system",
    # tasks / media / finance / memory
    "schedule": "tasks", "todo": "tasks",
    "spotify": "media",
    "finance": "finance",
    "vault_search": "memory", "index_doc": "memory", "note_append": "memory",
    # procedure — exposed only on explicit intent (see tool_router)
    "procedure_save": "procedure",
}

TOOL_SPECS = {
    name: replace(spec, domain=_TOOL_DOMAINS[name])
    for name, spec in TOOL_SPECS.items()
}

_missing = set(_TOOL_DOMAINS) - set(TOOL_SPECS)
if _missing:  # pragma: no cover — import-time wiring assertion
    raise RuntimeError(f"_TOOL_DOMAINS names unknown tools: {sorted(_missing)}")


def register_dynamic_spec(spec: ToolSpec) -> None:
    """Register a ToolSpec discovered at runtime (Faz 5: MCP tools) into the
    same dict the ~34 static @tool wrappers live in, so get_spec() -- and
    therefore policy_guard, the audit callback, and the async scheduler,
    which all only ever call get_spec()/read TOOL_SPECS -- cover it
    identically with zero changes to any of them. Idempotent: re-registering
    an existing name (e.g. a reconnect) just overwrites that entry."""
    TOOL_SPECS[spec.name] = spec


# ── Alpha capability allowlist (Agent Runtime rev.2, Faz 0) ──────────────────
# Reviewer item #9: every capability exposed during the manual alpha must
# carry an explicit status -- "args_schema=None and nobody decided" is not
# an acceptable default. Kept as a standalone dict rather than a ToolSpec
# field for now (same shape as _TOOL_DOMAINS above: one place, no per-ctor
# edits) because Faz 1 is where ToolSpec itself grows new fields
# (args_schema, postconditions, idempotency, contract_status, ...) -- adding
# a field here too, ahead of that, would mean touching the frozen dataclass
# twice. This dict is the seed that Faz 1 folds into ToolSpec.contract_status.
#
# Honesty note: "shadow_validated" below is the TARGET state once Faz 1's
# execution envelope lands, not a live signal today -- nothing in this repo
# yet measures or records shadow validation. Every tool not listed in
# _ALPHA_STATUS defaults to "shadow_validated" for that reason: it is a
# declared destination, not a claim that validation is currently happening.
# The only two statuses that are true TODAY are "disabled" (python_run --
# actually removed from make_tools()'s returned list, see graph/tools.py)
# and "quarantined" (shell_run -- stays exposed, flagged for the extra
# scrutiny future phases will add; not yet a behavioral difference beyond
# the flag itself existing).
ALPHA_STATUS_VALUES = frozenset({
    "contract_enforced",       # full prepare/execute/postcondition pipeline (Faz 1-6 destination)
    "shadow_validated",        # envelope produced and measured, decisions unchanged (Faz 1 destination)
    "quarantined",             # exposed, but flagged for extra scrutiny / no expanded trust
    "disabled",                # not exposed to the model at all -- structurally absent
    "explicitly_unverifiable", # no deterministic postcondition exists for this capability's
                                # notion of success (e.g. web_search) -- reported, not hidden
})

_ALPHA_STATUS: dict[str, str] = {
    # python_run: unsandboxed, arbitrary-length Python in a subprocess. Unlike
    # shell_run (a single command line a human can actually read before
    # approving), a multi-line script cannot be meaningfully reviewed in a
    # confirmation prompt. Disabled for the alpha surface, not sandboxed --
    # see ToolSpec's own docstring above python_run's entry for why a sandbox
    # is a separate, not-yet-built project. Enforced in two independent
    # places (defense in depth, not redundancy for its own sake): removed
    # from make_tools()'s returned list (graph/tools.py) so the model never
    # sees its schema, AND vetoed in policy_guard.evaluate() (allowed=False)
    # so a call somehow reaching the gate anyway -- a stale checkpoint
    # recorded before this change, say -- is still hard-blocked.
    "python_run": "disabled",
    # shell_run: same L3/confirmation gate as python_run, but a single
    # command line IS something a human can read and judge in the
    # confirmation prompt before approving -- kept on the alpha surface,
    # explicitly flagged for the extra scrutiny later phases add (owner may
    # revisit this call; see the plan's open-questions section).
    "shell_run": "quarantined",
}

_bad_values = {v for v in _ALPHA_STATUS.values() if v not in ALPHA_STATUS_VALUES}
if _bad_values:  # pragma: no cover -- import-time wiring assertion
    raise RuntimeError(f"_ALPHA_STATUS uses unknown status value(s): {sorted(_bad_values)}")
_unknown_names = set(_ALPHA_STATUS) - set(TOOL_SPECS)
if _unknown_names:  # pragma: no cover -- import-time wiring assertion
    raise RuntimeError(f"_ALPHA_STATUS names unknown tools: {sorted(_unknown_names)}")


def get_alpha_status(tool_name: str) -> str:
    """This tool's alpha-allowlist status -- see ALPHA_STATUS_VALUES and the
    module comment above _ALPHA_STATUS for what each value means and which
    ones are live behavior today vs. a declared future destination."""
    return _ALPHA_STATUS.get(tool_name, "shadow_validated")


# Convenience views ──────────────────────────────────────────────────────────────

def tools_at_risk(level: int) -> list[ToolSpec]:
    """Return specs whose risk_level equals *level*."""
    return [s for s in TOOL_SPECS.values() if s.risk_level == level]


def tools_requiring_confirmation() -> list[ToolSpec]:
    """Return specs where requires_confirmation is True."""
    return [s for s in TOOL_SPECS.values() if s.requires_confirmation]
