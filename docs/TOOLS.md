# J.A.R.V.I.S. — Tool Registry

35 tools registered by `make_tools()` in `jarvis/graph/tools.py`.
Formal specs live in `jarvis/tool_registry.py` (`ToolSpec` dataclass + `TOOL_SPECS` dict).

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
| `python_run` | L2 | compute | — | ✓ | 120s | Execute a Python script in a subprocess |
| `data_analyze` | L1 | compute | — | ✓ | 60s | Full pandas statistical analysis of a tabular file |
| `plot_data` | L2 | compute | — | ✓ | 60s | Generate a matplotlib/seaborn PNG and save to workspace |
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
| `finance` | L2 | external_api | — | ✓ | 60s | Burgan Bank finance sync (Gmail read-only), summary, budgets |
| `gcp_quota` | L1 | network | — | — | 30s | GCP Vertex AI quota status and usage tracking |
| `google_calendar` | L3 | external_api | ✓ | — | 30s | Calendar: list/search (L1) · create/update/delete (L3) |
| `gmail` | L3 | external_api | ✓ | — | 30s | Gmail: list/read/search (L1) · send/reply/trash (L3) |
| `google_drive` | L3 | external_api | ✓ | ✓ | 60s | Drive: search/read/download (L1) · upload/share/delete (L3) |
| `itu_mail` | L3 | external_api | ✓ | — | 30s | ITU IMAP/SMTP: list/read/search (L1) · send/reply/trash (L3) |

> **Phase 3** will insert a LangGraph `confirmation` node that reads `requires_confirmation` and
> `risk_level` from `jarvis/tool_registry.py` to interrupt before L3 tool calls across all modes
> (CLI, API, voice, mobile). See [ARCHITECTURE.md](ARCHITECTURE.md).
