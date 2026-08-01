# J.A.R.V.I.S. — Tool Registry

36 native tools registered by `make_tools()` in `jarvis/graph/tools.py`, plus (Faz 5) a dynamic
MCP layer — see below. Formal specs live in `jarvis/tool_registry.py` (`ToolSpec` dataclass +
`TOOL_SPECS` dict); MCP tools are inserted into that same dict at connect time via
`register_dynamic_spec()`, not listed in the dict's literal source.

## Risk levels

| Level | Label | Meaning |
|---|---|---|
| L1 | read / analysis | No writes, no external side-effects |
| L2 | reversible write | Local files / DB, local compute, reversible external (e.g. Spotify play) |
| L3 | external effect | Email send, calendar create/delete, Drive upload/delete, shell exec |
| L4 | destructive | (none currently — L3 shell covered by DENY_PATTERNS) |

## Tool specs

| Tool | Risk | Category | Confirm? | Background? | Timeout | Description |
|---|---|---|---|---|---|---|
| `shell_run` | L3 | compute | ✓ | ✓ | 120s | Run a PowerShell command (deny-list blocks destructive ops) |
| `file_read` | L1 | filesystem | — | — | 30s | Read a text file from the workspace |
| `file_write` | L2 | filesystem | — | — | 30s | Write or overwrite a file (creates parent dirs) |
| `file_list` | L1 | filesystem | — | — | 10s | List files and directories at a path |
| `pdf_read` | L1 | filesystem | — | ✓ | 60s | Extract text from a PDF (marker-pdf + pdfplumber fallback) |
| `pdf_vision` | L1 | network | — | ✓ | 60s | Analyse a PDF or image visually via Gemini Vision |
| `excel_read` | L1 | filesystem | — | — | 30s | Read an Excel file — column list + first 50 rows per sheet |
| `csv_read` | L1 | filesystem | — | — | 10s | Preview a CSV file (shape + first N rows) |
| `python_run` | L3 | compute | ✓ | ✓ | 120s | Execute a Python script in a subprocess |
| `data_analyze` | L1 | compute | — | ✓ | 60s | Full pandas statistical analysis of a tabular file |
| `plot_data` | L2 | compute | — | ✓ | 60s | Generate a matplotlib/seaborn PNG from a file (`sheet=` selects a worksheet) or inline `data_json` |
| `report_write` | L2 | filesystem | — | — | 30s | Write a LaTeX .tex source file to vault/reports/ |
| `report_compile` | L2 | compute | — | ✓ | 120s | Compile a .tex file to PDF via pdflatex |
| `report_compose` | L2 | compute | — | ✓ | 120s | Write markdown + figures into a LaTeX report PDF |
| `note_append` | L2 | filesystem | — | — | 10s | Append markdown text to a vault note file |
| `vault_search` | L1 | memory | — | — | 15s | Semantic RAG search over indexed vault documents |
| `index_doc` | L2 | memory | — | ✓ | 60s | Index a document into the jarvis_docs ChromaDB collection |
| `web_search` | L1 | network | — | — | 30s | Quick Tavily web search returning snippets |
| `url_read` | L1 | network | — | — | 30s | Fetch a URL and return clean article text |
| `deep_web_research` | L1 | network | — | ✓ | 120s | Multi-step Tavily search + fetch + Gemini Pro synthesis |
| `math_solve` | L2 | sub_agent | — | ✓ | 120s | MathAgent: symbolic + numerical math (SymPy / Gemini) |
| `write_content` | L2 | sub_agent | — | ✓ | 120s | WriterAgent: long-form text generation |
| `research` | L1 | sub_agent | — | ✓ | 120s | ResearchAgent: multi-source web research with citations |
| `generate_code` | L2 | sub_agent | — | ✓ | 120s | CoderAgent: code generation and explanation |
| `geo_math` | L2 | sub_agent | — | ✓ | 180s | GeoMathAgent: SymPy + Devito FDM + Plotly/PyVista |
| `spotify` | L2 | external_api | — | — | 15s | Play/pause/resume/next/previous/current via Spotify Web API |
| `hud_panels` | L2 | ui | — | — | 5s | Show/hide/toggle panels in the Electron HUD |
| `schedule` | L2 | compute | — | — | 10s | Scheduled tasks and reminders (SQLite) |
| `todo` | L2 | compute | — | — | 30s | To-do list with LLM priority analysis (SQLite) |
| `finance` | L2 | external_api | — | ✓ | 60s | Bank mail sync (Gmail read-only), **`import_statement`** (PDF ekstre → ledger), cash-flow summary, budgets, **`export`** → multi-sheet .xlsx + chart |
| `gcp_quota` | L1 | network | — | — | 30s | GCP Vertex AI quota status and usage tracking |
| `google_calendar` | L3 | external_api | ✓* | — | 30s | Calendar: list/search (L1) · create/update/delete (L3). **\*** a SINGLE high-confidence `create` skips the prompt (Post-MVP Faz 2) — see below |
| `gmail` | L3 | external_api | ✓ | — | 30s | Gmail: list/read/search (L1) · send/reply/trash (L3) |
| `google_drive` | L3 | external_api | ✓ | ✓ | 60s | Drive: search/read/download (L1) · upload/share/delete (L3) |
| `itu_mail` | L3 | external_api | ✓ | — | 30s | ITU IMAP/SMTP: list/read/search (L1) · send/reply/trash (L3) |
| `procedure_save` | L2 | memory | — | — | 15s | Save a reusable multi-step workflow to procedural memory (Faz 2) |

## Independently verified tools (Post-MVP Faz 1, 2026-07-31)

Most tools report their own success and are believed. These six are checked against the
filesystem afterwards, so a tool that says "written" and a file that is not there produce
different outcomes:

| Tool | Postcondition | How the path is found |
|---|---|---|
| `file_write` | `file_exists` + `path_within_workspace` | Its `path` argument IS the destination |
| `plot_data` | `declared_artifacts_exist` | Declared by `generate_plot` after `savefig` — `output` is only a filename STEM, and collisions append `_1`/`_2`, so the argument never identified the real file |
| `report_write` | `declared_artifacts_exist` | Declared after write — the path is derived from `title` |
| `report_compose` | `declared_artifacts_exist` | Declared after write |
| `report_compile` | `declared_artifacts_exist` | Declared after the PDF is copied out of the temp dir, so what is verified is the file the user can open |
| `finance` | `declared_artifacts_exist` | `export` declares the `.xlsx`; its embedded chart declares itself inside `generate_plot`. Read-only actions declare nothing and honestly report "unverified" |

Verdicts: all declared files present → `confirmed`; **any declared file missing → the tool's
reported success is downgraded and surfaced to the user**; nothing declared → `unverified`, never
a silent pass. Every other tool in the table above stays honestly "not independently verified".
Mechanism: `jarvis/execution/artifacts.py` (declaration) and
`jarvis/execution/postcondition_runner.py` (checking); see [SAFETY.md](SAFETY.md).

## Calendar: the one action that can skip its prompt (Post-MVP Faz 2, 2026-07-31)

`google_calendar` is still L3 `external_write` for every action. What changed is **when the user is
asked**, and only for a single `create`:

| Situation | Behaviour |
|---|---|
| `create`, date + time + title + the user's own wording all ≥ 0.95 confidence | runs, no prompt |
| `create`, anything ambiguous (bare weekday, bare small hour, instruction-shaped title) | asks |
| `create`, the user named a weekday the given date is not | asks (provable contradiction) |
| `batch_create` · `update` · `delete` | asks, always, however confident |
| Kill switch tripped · `--profile test` · background/proactive turn · workflow step | unchanged — none of these can reach the auto path |

Confidence is **derived** by `policy_guard.calendar_confidence()` from the arguments and
`state["user_query"]`, never read from an argument — a `confidence` field in the schema would let
the model approve its own actions. Turn it all off with `calendar_autonomy_enabled=False`.

Dates and times are resolved by `jarvis/nlu/temporal.py`, in `settings.calendar_timezone`, not by
the model: pass the user's wording through (`date="yarın"`, `time="öğlen 3"`) rather than computing
a date. `öğlen 3` is 15:00. An expression naming a period rather than a day (`haftaya`) is refused
with a reason instead of guessed.

## MCP tools (Faz 5, dynamic — `jarvis/mcp_integration.py`)

Not in `make_tools()`/the table above — discovered at connect time from configured MCP servers
and registered the same way. Only enabled when `MCP_PLAYWRIGHT_ENABLED=True` (or a server is added
to `MCP_SERVERS`) in `.env`; ships disabled by default. Names below are the real, live-verified
(2026-07-15) 24-tool set from `@playwright/mcp@latest`'s core capability (opt-in extras — storage,
network mocking, devtools, PDF, testing — are not enabled).

| Tool | Risk | Confirm? | Why |
|---|---|---|---|
| `browser_snapshot`, `browser_take_screenshot`, `browser_console_messages`, `browser_network_requests`, `browser_network_request`, `browser_find` | L1 | — | Pure inspection, no page mutation |
| `browser_navigate`, `browser_navigate_back`, `browser_wait_for`, `browser_resize`, `browser_close`, `browser_tabs` | L2 | — | Navigation / tab management, no lasting external effect |
| `browser_click`, `browser_type`, `browser_fill_form`, `browser_press_key`, `browser_select_option`, `browser_file_upload`, `browser_drag`, `browser_drop`, `browser_hover`, `browser_handle_dialog`, `browser_evaluate`, `browser_run_code_unsafe`, *any future/unrecognized tool name* | L3 | ✓ | Fail-closed default — can submit forms, run arbitrary JS, or act on a page whose content the model doesn't control (prompt-injection surface) |

Classification lives in `jarvis/mcp_integration.py`'s `_classify()` — a short explicit allow-list
for the left two rows, everything else falls through to the fail-closed default. This mirrors
`ToolSpec.actions`' per-action override pattern (Faz 2.75, Paket E — formerly
`policy_guard._READ_ACTIONS`) for the mixed-risk Google/
ITU tools above, just keyed by MCP tool name instead of an `action` argument.

> **Faz 4 (2026-07-14) finished what Phase 3 started**: `jarvis/graph/nodes.py`'s
> `make_confirmation_node` now gates through `jarvis/policy_guard.py` — per-*action*, not
> per-tool, so read actions (list/search/...) on the four `external_api` tools above never
> interrupt even though the tool itself is L3. The gate is on by default
> (`confirmation_gate_enabled=True`) and wired into all three interfaces (CLI text, CLI/API
> voice, API) — see [SAFETY.md](SAFETY.md) for the full picture, including a kill switch,
> an audit log, and the honest list of what's still not done (no Electron/mobile confirmation
> UI yet).
