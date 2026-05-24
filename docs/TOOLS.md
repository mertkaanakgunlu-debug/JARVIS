# J.A.R.V.I.S. — Tool Registry

35 tools registered by `make_tools()` in `jarvis/graph/tools.py`.

Risk levels: **L1** read-only · **L2** reversible local write · **L3** external side-effect

| Tool name | Risk | Category | Description |
|---|---|---|---|
| `file_read` | L1 | Files | Read a file from the workspace |
| `file_list` | L1 | Files | List files / directories in the workspace |
| `file_write` | L2 | Files | Write or overwrite a file in the workspace |
| `pdf_read` | L1 | Files | Extract text from a PDF (marker-pdf → pdfplumber fallback) |
| `pdf_vision` | L1 | Files | Analyse a PDF page visually via Gemini Vision |
| `excel_read` | L1 | Files | Read an Excel file with auto header detection |
| `csv_read` | L1 | Files | Read a CSV file |
| `data_analyze` | L1 | Data | Statistical summary of tabular data |
| `plot_data` | L2 | Data | Generate a matplotlib chart and save to workspace |
| `python_run` | L2 | Code | Execute a Python script in a subprocess (60s timeout) |
| `shell_run` | L3 | System | Run a shell command (deny-list blocks destructive ops) |
| `note_append` | L2 | Vault | Append text to a vault note (`vault/notes/{topic}.md`) |
| `vault_search` | L1 | Vault | Semantic search over indexed vault documents |
| `index_doc` | L2 | Vault | Index a document into the `jarvis_docs` ChromaDB collection |
| `web_search` | L1 | Web | Tavily web search |
| `url_read` | L1 | Web | Extract article text from a URL (trafilatura) |
| `deep_web_research` | L1 | Web | Multi-step research via Tavily + URL extraction |
| `report_write` | L2 | Reports | Write a LaTeX source file to `vault/reports/` |
| `report_compile` | L2 | Reports | Compile LaTeX → PDF via pdflatex |
| `report_compose` | L2 | Reports | Write + compile in one step |
| `google_calendar` | L1/L3 | Google | List/search (L1); create/update/delete (L3) |
| `gmail` | L1/L3 | Google | List/read/search (L1); send/reply/trash/mark_read (L3) |
| `google_drive` | L1/L3 | Google | Search/list/read/download (L1); upload/share/delete (L3) |
| `itu_mail` | L1/L3 | ITU | List/read/search IMAP (L1); send/reply/trash SMTP (L3) |
| `schedule` | L1/L2 | Scheduler | List (L1); add/pause/resume/done/delete (L2) |
| `todo` | L1/L2 | Todos | List/today (L1); add/edit/done/delete/analyze (L2) |
| `finance` | L1/L2 | Finance | Summary/recent/budget_status (L1); sync/set_budget/chart (L2) |
| `spotify` | L2 | Media | Play/pause/resume/next/previous/current (Spotify Web API) |
| `gcp_quota` | L1 | Cloud | Read GCP Vertex AI quota and usage |
| `hud_panels` | L2 | HUD | Show/hide Electron HUD panels |
| `math_solve` | L2 | Sub-agent | MathAgent: symbolic + numerical math (pydantic-ai) |
| `write_content` | L2 | Sub-agent | WriterAgent: long-form text generation (pydantic-ai) |
| `research` | L2 | Sub-agent | ResearchAgent: multi-source web research (pydantic-ai) |
| `generate_code` | L2 | Sub-agent | CoderAgent: code generation + explanation (pydantic-ai) |
| `geo_math` | L2 | Sub-agent | GeoMathAgent: geophysics math + FDM simulation (pydantic-ai) |

> **Phase 2** will add a `ToolSpec` dataclass with formal `risk_level`, `requires_confirmation`,
> `side_effect_type`, `allowed_modes`, and `timeout_seconds` for each tool. See [ARCHITECTURE.md](ARCHITECTURE.md).
