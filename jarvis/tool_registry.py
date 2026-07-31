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
from typing import Literal

from jarvis.execution.postcondition import PostconditionSpec
from jarvis.execution import args_schemas  # Agent Runtime rev.2, Faz 6


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

    # ── Agent Runtime rev.2, Faz 1: additive fields, every one defaulted so
    # none of the 36 existing ToolSpec(...) calls below need to change.
    # Every field here is a declared FUTURE destination, not a live signal
    # today (same honesty discipline as contract_status below) — nothing
    # reads args_schema/postconditions/idempotency/effect_scope/
    # timeout_class yet.
    args_schema: type | None = None
        # Faz 6 destination: a pydantic BaseModel class for structural
        # argument validation. None = not yet typed (every tool today).
    postconditions: tuple = ()
        # tuple[jarvis.execution.postcondition.PostconditionSpec, ...] --
        # Faz 3 destination (the postcondition runner). Empty = none
        # declared yet for any tool.
    idempotency: Literal["none", "natural", "keyed"] = "none"
        # Classified per-tool via _IDEMPOTENCY below (Faz 7.3) -- the
        # dataclass default stays "none" so dynamically-registered MCP specs
        # (register_dynamic_spec) are fail-closed: an unknown external tool
        # must never be presumed safe to re-run after a crash.
    effect_scope: Literal["unclassified", "reversible", "irreversible"] = "unclassified"
        # Faz 7 destination (compensation eligibility -- "yalniz kayitli
        # gercek tersi olan islemlerde otomatik telafi"). Deliberately NOT
        # derived from side_effect_type/risk_level here: getting this wrong
        # now would need re-deciding in Faz 7 anyway, so it stays honestly
        # unclassified until that phase does the real per-tool pass.
    contract_status: str = "shadow_validated"
        # Folded in below from the Faz 0 alpha allowlist (_ALPHA_STATUS) --
        # see get_alpha_status(). Kept as plain str, not
        # Literal[ALPHA_STATUS_VALUES], because a dataclass field can't
        # reference a runtime frozenset in its type annotation; the
        # import-time guard a few lines below _ALPHA_STATUS is the actual
        # enforcement.
    timeout_class: Literal[
        "cooperative_async", "soft_thread_timeout",
        "hard_process_timeout", "external_request_timeout",
    ] = "cooperative_async"
        # Faz 3 destination (real per-class timeout enforcement --
        # ToolSpec.timeout_seconds is declared but NOT enforced by anything
        # today, per the plan's root-cause section). Defaulting every tool
        # to the loosest class rather than hand-classifying 36 tools now:
        # Faz 3 does that classification pass together with actually
        # wiring enforcement, so a premature guess here would just be
        # re-decided then anyway.


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
        # Agent Runtime rev.2, Faz 3: file_write is the one file-producing
        # tool with an unambiguous, directly-checkable output path -- its
        # own `path` argument IS the real destination (unlike plot_data's
        # `output`, a filename STEM, or report_write's title-derived path).
        # Other file-producing tools are deliberately left unattached this
        # phase -- see postcondition_runner.py's module docstring for why.
        postconditions=(
            PostconditionSpec(kind="file_exists", params={"path_arg": "path"}, source="tool_contract"),
            PostconditionSpec(kind="path_within_workspace", params={"path_arg": "path"}, source="policy"),
        ),
        # Faz 7: the one tool with a real, registered compensator
        # (jarvis.execution.workflow_engine's _COMPENSATORS -- restore prior
        # content, or delete a newly-created file). Every other tool stays
        # "unclassified" -- see that module's docstring for why this field
        # isn't blanket-populated across all 36 tools this phase.
        effect_scope="reversible",
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
        args_schema=args_schemas.PlotDataArgs,
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
        args_schema=args_schemas.GeoMathArgs,
    ),

    # ── External APIs — reversible (L2) ──────────────────────────────────────────
    ToolSpec(
        "spotify", "external_api", 2, False, "external_write",
        timeout_seconds=15,
        description="Play/pause/resume/next/previous/current via Spotify Web API",
        args_schema=args_schemas.SpotifyArgs,
    ),
    ToolSpec(
        "hud_panels", "ui", 2, False, "none",
        timeout_seconds=5,
        description="Show/hide/toggle panels in the Electron HUD",
        args_schema=args_schemas.HudPanelsArgs,
    ),
    ToolSpec(
        "schedule", "compute", 2, False, "local_write",
        timeout_seconds=10,
        description="Scheduled tasks and reminders stored in SQLite",
        args_schema=args_schemas.ScheduleArgs,
    ),
    ToolSpec(
        "todo", "compute", 2, False, "local_write",
        timeout_seconds=30,
        description="To-do list with LLM priority analysis, stored in SQLite",
        args_schema=args_schemas.TodoArgs,
    ),
    ToolSpec(
        # side_effect_type is "local_write", not "external_read", as of
        # 2026-07-30: action="export" writes an .xlsx into the workspace, so
        # "external_read" understated this tool's maximum real effect. The field
        # must describe the WORST thing the tool can do, not its most common
        # action. Two consumers change behavior accordingly, both in the safer
        # direction: workflow_engine._had_side_effect() now treats a finance step
        # as mutating (relevant to partial-commit/compensation), and the
        # EXTERNAL_WRITES_ENABLED=false block is unaffected (it keys on
        # "external_write", which this is not -- so a read-only live run can
        # still sync and export). Risk level stays L2: a local, overwritable file
        # in the workspace is a reversible write, and there is no
        # policy_guard._READ_ACTIONS entry because finance has no per-action risk
        # split of the kind gmail/calendar/drive have.
        "finance", "external_api", 2, False, "local_write",
        timeout_seconds=60, supports_background=True,
        description=(
            "Bank mail sync (Gmail read-only), cash-flow summary, budget tracking, "
            "and Excel (.xlsx) cash-flow workbook export"
        ),
        args_schema=args_schemas.FinanceArgs,
    ),
    ToolSpec(
        "gcp_quota", "network", 1, False, "external_read",
        timeout_seconds=30,
        description="GCP Vertex AI quota status and usage tracking",
        args_schema=args_schemas.GcpQuotaArgs,
    ),

    # ── External APIs — side-effect (L3) ─────────────────────────────────────────
    ToolSpec(
        "google_calendar", "external_api", 3, True, "external_write",
        timeout_seconds=30,
        description="Google Calendar: list/search (L1 actions) + create/update/delete (L3 actions)",
        args_schema=args_schemas.GoogleCalendarArgs,
    ),
    ToolSpec(
        "gmail", "external_api", 3, True, "external_write",
        timeout_seconds=30,
        description="Gmail: list/read/search (L1 actions) + send/reply/trash/mark_read (L3 actions)",
        args_schema=args_schemas.GmailArgs,
    ),
    ToolSpec(
        "google_drive", "external_api", 3, True, "external_write",
        timeout_seconds=60, supports_background=True,
        description="Google Drive: search/read/download (L1 actions) + upload/share/delete (L3 actions)",
        args_schema=args_schemas.GoogleDriveArgs,
    ),
    ToolSpec(
        "itu_mail", "external_api", 3, True, "external_write",
        timeout_seconds=30,
        description="ITU webmail IMAP/SMTP: list/read/search (L1 actions) + send/reply/trash (L3 actions)",
        args_schema=args_schemas.ItuMailArgs,
    ),

    # ── Faz 2: cognitive memory ────────────────────────────────────────────────
    ToolSpec(
        "procedure_save", "memory", 2, False, "local_write",
        timeout_seconds=15,
        description="Save a reusable multi-step workflow to procedural memory",
    ),

    # ── Agent Runtime rev.2, Faz 7 Part 2: workflow runtime, live-wired ─────────
    ToolSpec(
        # Deliberately requires_confirmation=True as defense-in-depth ON TOP
        # of jarvis.execution.workflow_engine's own PER-STEP gating (each
        # step independently runs policy_guard.evaluate() and pauses for its
        # own approval when needed): kicking off an autonomous multi-step
        # process is a meaningfully bigger action than one tool call, even
        # though nothing it does can bypass its own steps' individual gates.
        "workflow_start", "sub_agent", 2, True, "local_write",
        timeout_seconds=300,
        description="Start a multi-step workflow (dependency-ordered steps, its own approval/"
                     "compensation) for a task too large for one turn's tool-call budget",
    ),
    ToolSpec(
        "workflow_status", "sub_agent", 1, False, "local_read",
        timeout_seconds=10,
        description="Report a workflow's current step-by-step status",
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
    # data / report / math — split out of one oversized "data" domain on
    # 2026-07-30. That domain held exactly 8 tools while the router's
    # MAX_TOOLS_PER_TURN is also 8, so whenever "data" was the primary domain it
    # consumed the entire per-turn budget and NO second domain could ever be
    # added. Measured consequence: the owner's MVP prompt ("Maillerimi kontrol
    # et, hesabimdaki para akisini analiz et, bir excel tablosuna donustur ve
    # grafikle") routed to [data, mail] and exposed 8 data tools with gmail and
    # finance both invisible -- the task was impossible for any model, and the
    # model duly reported on a mailbox it had no way to read.
    #
    # Raising the cap would have treated the symptom. "data" was a junk drawer
    # holding analysis, charting, LaTeX reports, prose generation and symbolic
    # maths -- five unrelated intents. Splitting it fixes the class: no single
    # domain is near the cap any more.
    "data_analyze": "data", "plot_data": "data",
    "report_write": "report", "report_compile": "report",
    "report_compose": "report", "write_content": "report",
    "math_solve": "math", "geo_math": "math",
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
    # workflow — exposed only on explicit intent (see tool_router); Faz 7 Part 2
    "workflow_start": "workflow", "workflow_status": "workflow",
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


# Faz 1: fold the same data into ToolSpec.contract_status too -- "this dict
# is the seed that Faz 1 folds into ToolSpec.contract_status" (comment above
# _ALPHA_STATUS, written in Faz 0). get_alpha_status() stays the accessor
# the Faz 0 tests already exercise (untouched, same _ALPHA_STATUS dict
# underneath); this just makes the same fact reachable via
# get_spec(name).contract_status for code that already has a ToolSpec in
# hand (Faz 1's shadow envelope construction) without a second lookup.
# Dynamically-registered specs (register_dynamic_spec(), Faz 5 MCP tools)
# don't go through this one-time pass, but need no special-casing: their
# names are never in _ALPHA_STATUS, so the dataclass field's own default
# ("shadow_validated") already matches what get_alpha_status() would
# return for them.
TOOL_SPECS = {
    name: replace(spec, contract_status=get_alpha_status(name))
    for name, spec in TOOL_SPECS.items()
}


# ── Agent Runtime rev.2, Faz 3: timeout_class classification ─────────────────
# Same "one place, import-time-checked" shape as _TOOL_DOMAINS/_ALPHA_STATUS
# above. Every STATIC tool must appear here -- ToolSpec.timeout_class
# defaulted to "cooperative_async" for every tool since Faz 1 (the loosest,
# most-trusting class), which was honestly wrong for most of them; this is
# the classification pass that makes the field a real signal instead of a
# uniform guess. Judged from each tool's actual execution shape:
#
#   hard_process_timeout      -- spawns a real subprocess (shell_run,
#                                 python_run, report_compile: pdflatex).
#                                 timeout_seconds is now threaded into the
#                                 underlying subprocess.run(timeout=...) call
#                                 itself (jarvis/tools/shell.py, python_exec.py,
#                                 latex.py) -- that inner call is what actually
#                                 kills the child; the outer wrap in
#                                 safe_tools.py is a defensive backstop only.
#   cooperative_async         -- native `async def` @tool whose body awaits
#                                 real I/O (the sub-agent bridges: math_solve/
#                                 write_content/research/generate_code delegate
#                                 to an LLM via ainvoke(); todo's blocking work
#                                 is local SQLite, fast enough that this class
#                                 is still the honest fit). asyncio.wait_for
#                                 genuinely cancels these at their next await
#                                 point.
#   external_request_timeout -- blocks on a REMOTE network round-trip (a
#                                 cloud API or IMAP/SMTP session) inside a
#                                 sync @tool body, dispatched through ToolNode's
#                                 executor thread. Honesty note: real
#                                 client-level (per-library) timeouts are NOT
#                                 wired this phase for any of these -- only the
#                                 generic outer asyncio.wait_for bound applies,
#                                 same enforcement shape as soft_thread_timeout
#                                 (a timeout here stops the AWAIT, not the
#                                 underlying socket call). Classified
#                                 separately anyway because "should eventually
#                                 get a real client-level timeout" is a
#                                 meaningfully different backlog item than
#                                 "runs local compute in a thread" -- collapsing
#                                 the two would lose that distinction.
#   soft_thread_timeout       -- everything else: local CPU/filesystem/SQLite/
#                                 ChromaDB work in a sync @tool body. A timeout
#                                 stops the AWAIT; the executor thread keeps
#                                 running to completion in the background
#                                 (execution_may_still_be_running=true is the
#                                 honest report, not a bug).
#
# geo_math is a deliberately CONSERVATIVE call: its "analyze" action really is
# cooperative (delegates to an async sub-agent), but every other action
# (wave_simulate_2d, plot_contour, plot_3d_surface, plot_volume) runs heavy
# SYNCHRONOUS compute directly inside the `async def` body with no await point
# at all -- worse than soft_thread_timeout if taken literally (a genuinely long
# call there blocks the whole event loop, not just one executor thread), but
# there is no dedicated vocabulary slot for that failure mode and a full
# refactor to run that compute in a real executor thread is out of scope for
# this phase. soft_thread_timeout is the closest honest label available today.
_TIMEOUT_CLASSES: dict[str, str] = {
    # hard_process_timeout -- real subprocess spawns
    "shell_run": "hard_process_timeout",
    "python_run": "hard_process_timeout",
    "report_compile": "hard_process_timeout",
    # cooperative_async -- native async @tool, real await points
    "math_solve": "cooperative_async",
    "write_content": "cooperative_async",
    "research": "cooperative_async",
    "generate_code": "cooperative_async",
    "todo": "cooperative_async",
    # external_request_timeout -- remote network round-trip
    "pdf_vision": "external_request_timeout",
    "web_search": "external_request_timeout",
    "url_read": "external_request_timeout",
    "deep_web_research": "external_request_timeout",
    "spotify": "external_request_timeout",
    "google_calendar": "external_request_timeout",
    "gmail": "external_request_timeout",
    "google_drive": "external_request_timeout",
    "itu_mail": "external_request_timeout",
    "finance": "external_request_timeout",
    "gcp_quota": "external_request_timeout",
    # soft_thread_timeout -- local compute/filesystem/SQLite/ChromaDB
    "file_read": "soft_thread_timeout",
    "file_write": "soft_thread_timeout",
    "file_list": "soft_thread_timeout",
    "pdf_read": "soft_thread_timeout",
    "excel_read": "soft_thread_timeout",
    "csv_read": "soft_thread_timeout",
    "data_analyze": "soft_thread_timeout",
    "plot_data": "soft_thread_timeout",
    "report_write": "soft_thread_timeout",
    "report_compose": "soft_thread_timeout",
    "note_append": "soft_thread_timeout",
    "vault_search": "soft_thread_timeout",
    "index_doc": "soft_thread_timeout",
    "geo_math": "soft_thread_timeout",
    "hud_panels": "soft_thread_timeout",
    "schedule": "soft_thread_timeout",
    "procedure_save": "soft_thread_timeout",
    "workflow_start": "soft_thread_timeout",
    "workflow_status": "soft_thread_timeout",
}

_missing_timeout_class = set(TOOL_SPECS) - set(_TIMEOUT_CLASSES)
if _missing_timeout_class:  # pragma: no cover -- import-time wiring assertion
    raise RuntimeError(f"_TIMEOUT_CLASSES missing tool(s): {sorted(_missing_timeout_class)}")
_unknown_timeout_class_names = set(_TIMEOUT_CLASSES) - set(TOOL_SPECS)
if _unknown_timeout_class_names:  # pragma: no cover -- import-time wiring assertion
    raise RuntimeError(f"_TIMEOUT_CLASSES names unknown tools: {sorted(_unknown_timeout_class_names)}")

TOOL_SPECS = {
    name: replace(spec, timeout_class=_TIMEOUT_CLASSES[name])
    for name, spec in TOOL_SPECS.items()
}


# ── Faz 7.3 (P0): idempotency classification ─────────────────────────────────
# Same "one place, import-time-checked" shape as _TOOL_DOMAINS/_TIMEOUT_CLASSES.
# This field is the SINGLE authority WorkflowEngine's crash recovery consults
# when a step is found "running" with no idempotency-journal commit -- i.e.
# the process died somewhere between dispatch and journal write, and nobody
# knows whether the side effect landed:
#
#   natural -- re-running the SAME call converges to the same end state, so a
#              blind retry is safe. True for every pure read, for fixed-
#              content overwrites (file_write, report_write: title-derived
#              path), and for writers with verified content-dedup on re-run
#              (index_doc: deletes stale chunks by source + deterministic
#              chunk ids; finance sync: add_transaction skips on existing
#              email_uid; procedure_save: fingerprint-based add_or_get --
#              the F16 incident fix).
#   none    -- re-running may duplicate or compound the effect. gmail/
#              itu_mail send, google_calendar create, google_drive upload
#              (the exact double-send class this classification exists to
#              stop), but also honestly-non-idempotent local ops:
#              note_append (append twice = duplicated text), todo/schedule
#              "add" (second row), plot_data (counter-suffixes a NEW file
#              rather than overwriting -- verified in plotting.py, not
#              assumed), spotify ("next" twice skips two tracks),
#              hud_panels ("toggle" twice = back where it started),
#              workflow_start (a second whole workflow), and arbitrary
#              execution (shell_run/python_run/geo_math -- geo_math's
#              non-analyze branches write files with unverified naming;
#              fail-closed).
#   keyed   -- the service accepts a client idempotency key, and the
#              dispatch layer passes execution_id as that key. Declared
#              destination, NOT live: no tool implementation accepts a key
#              today. google_calendar (client-generated event ids) is the
#              first real candidate when someone wires it -- reclassify to
#              "keyed" in the same commit that threads the key through the
#              tool body, never before.
_IDEMPOTENCY: dict[str, str] = {
    # natural -- pure reads
    "file_read": "natural", "file_list": "natural", "pdf_read": "natural",
    "pdf_vision": "natural", "excel_read": "natural", "csv_read": "natural",
    "data_analyze": "natural", "vault_search": "natural",
    "web_search": "natural", "url_read": "natural", "deep_web_research": "natural",
    "research": "natural", "gcp_quota": "natural", "workflow_status": "natural",
    # natural -- pure compute, no side effects
    "math_solve": "natural", "write_content": "natural", "generate_code": "natural",
    # natural -- converging writes (overwrite / verified dedup)
    "file_write": "natural", "report_write": "natural", "report_compile": "natural",
    "report_compose": "natural", "index_doc": "natural", "finance": "natural",
    "procedure_save": "natural",
    # none -- re-run may duplicate/compound
    "shell_run": "none", "python_run": "none", "geo_math": "none",
    "plot_data": "none", "note_append": "none",
    "schedule": "none", "todo": "none",
    "spotify": "none", "hud_panels": "none",
    "google_calendar": "none", "gmail": "none",
    "google_drive": "none", "itu_mail": "none",
    "workflow_start": "none",
}

_missing_idem = set(TOOL_SPECS) - set(_IDEMPOTENCY)
if _missing_idem:  # pragma: no cover -- import-time wiring assertion
    raise RuntimeError(f"_IDEMPOTENCY missing tool(s): {sorted(_missing_idem)}")
_unknown_idem_names = set(_IDEMPOTENCY) - set(TOOL_SPECS)
if _unknown_idem_names:  # pragma: no cover -- import-time wiring assertion
    raise RuntimeError(f"_IDEMPOTENCY names unknown tools: {sorted(_unknown_idem_names)}")

TOOL_SPECS = {
    name: replace(spec, idempotency=_IDEMPOTENCY[name])
    for name, spec in TOOL_SPECS.items()
}


# Convenience views ──────────────────────────────────────────────────────────────

def tools_at_risk(level: int) -> list[ToolSpec]:
    """Return specs whose risk_level equals *level*."""
    return [s for s in TOOL_SPECS.values() if s.risk_level == level]


def tools_requiring_confirmation() -> list[ToolSpec]:
    """Return specs where requires_confirmation is True."""
    return [s for s in TOOL_SPECS.values() if s.requires_confirmation]
