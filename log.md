# log.md — Project J.A.R.V.I.S.
Append-only chronological log. Never edit old entries.
Format: `## YYYY-MM-DD — title` → what was done, decisions, blockers.

---

## 2026-05-08 — Iteration 1 scaffolded

**Done:**
- Created full Iteration 1 directory structure and codebase from scratch
- Wrote all tracking files: README.md, ProjectState.md, log.md
- Implemented `jarvis/` Python package:
  - `config.py` — pydantic-settings from .env
  - `memory.py` — ChromaDB (nomic-embed-text via Ollama) + markdown vault writer
  - `agent.py` — Pydantic-AI agent with dual-backend hybrid routing
  - `cli.py` — Rich-themed REPL (gold/blue color scheme)
  - `tools/shell.py` — shell execution with deny-list + confirmation
  - `tools/files.py` — safe file ops scoped to workspace
  - `tools/notes.py` — vault note appender
  - `prompts/system.md` — JARVIS system prompt

**Decisions made:**
- Hybrid routing: local Qwen 2.5 7B (Ollama) default; Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) for `/think` prefix, messages >50 words, or complex-task keywords
- Embeddings: `nomic-embed-text` pulled via Ollama to keep full local ecosystem
- ChromaDB collection: `jarvis_memory`, persistent at `data/chroma/`
- Vault: Obsidian-compatible, daily conversation files at `vault/conversations/YYYY-MM-DD.md`

**Blockers (user action required):**
1. Install Ollama: https://ollama.com/download/windows
2. Pull models: `ollama pull qwen2.5:7b-instruct && ollama pull nomic-embed-text`
3. Install deps: `pip install -r requirements.txt`
4. Create `.env` from `.env.example` and add `ANTHROPIC_API_KEY`

**Next:**
- Complete install steps above
- Run smoke test: `python -m jarvis`
- Verify 7 end-to-end test cases (see ProjectState.md → Verification)
- If Qwen tool calls are unreliable, consider switching to `llama3.1:8b-instruct`

---

## 2026-05-08 — Swapped cloud backend: Anthropic → Gemini

**Reason:** User has Gemini Pro subscription for a year; no Anthropic API key available.

**Changes:**
- `config.py`: `anthropic_api_key` → `gemini_api_key`, cloud model default → `gemini-2.0-flash`, added `cloud_model_label` property
- `agent.py`: `AnthropicModel` → `GeminiModel`, fixed model-label display logic
- `requirements.txt`: swapped `anthropic` for `google-generativeai`
- `.env.example`: `ANTHROPIC_API_KEY` → `GEMINI_API_KEY`

**Decision:** `gemini-2.0-flash` as default (free, 1M tokens/day). Set `CLOUD_TIER=pro` in `.env` to use `gemini-1.5-pro` with the Pro subscription.

**User action required:**
1. `pip install -r requirements.txt` (re-run to get google-generativeai)
2. Get free key at https://aistudio.google.com/apikey
3. Copy `.env.example` → `.env`, set `GEMINI_API_KEY=AIza...`

---

## 2026-05-08 — Fixed Gemini cloud route (model name 404)

**Error:** `ModelHTTPError: status_code: 404, model_name: gemini-1.5-pro` when using `/think` prefix (pro tier).

**Root cause:** `gemini-1.5-pro` is not a valid identifier for the Gemini API v1beta endpoint pydantic-ai uses. The versioned/preview alias is required.

**Fix:**
- `config.py`: `effective_cloud_model` pro branch changed from `"gemini-1.5-pro"` → `"gemini-2.5-pro-preview-05-06"` (current Gemini 2.5 Pro preview, available to Gemini Pro subscribers)
- `config.py`: `cloud_model_label` updated to detect `"2.5-pro"` in model string
- `agent.py`: Added `from typing import Any` (missing import for `self._history: list[Any]`)

**Status:** All three routes should now work: local Qwen (default), Gemini 2.0 Flash (flash tier), Gemini 2.5 Pro (pro tier via `/think`).

---

## 2026-05-09 — Groq investigation, HW5 pipeline, excel auto-detection, /model command

### Groq TPM investigation

Tried activating Groq as the orchestrator to extend free-tier capacity.

**Models tested (via live API header probing `x-ratelimit-limit-tokens`):**

| Model | TPM limit | Outcome |
|---|---|---|
| qwen/qwen3-32b | 6 000 | System prompt + tool schemas ≈ 5422 tokens → Requested 6322 → over limit |
| openai/gpt-oss-120b | 8 000 | Input tokens grew to 5422 after CRITICAL section additions → over limit |
| meta-llama/llama-4-scout-17b-16e-instruct | 30 000 | Enough headroom, but 17B model hallucinates tool calls or refuses complex tasks |
| llama-3.3-70b-versatile | 12 000 | Would need max_tokens ≤ 6578; too tight for sub-agent delegation |

**Mitigation attempts:** reduced max_tokens (3000→2700→4000), memory recall n=5→n=2. Input tokens still ~5422 because of tool schemas for 15 tools.

**Decision:** GROQ_API_KEY commented out in .env. Groq only viable for simple 1-step queries; not suitable for multi-step orchestration with the current prompt size. GROQ_MODEL kept as `meta-llama/llama-4-scout-17b-16e-instruct` in case user wants to reactivate for voice-only path.

---

### tool_use_failed (400) bug on Groq

CoderAgent generated Python code with Unicode math symbols (`ρ` U+03C1, `−` U+2212) inside a `file_write` tool call. Groq's JSON serializer rejected non-ASCII characters in function call arguments.

**Fixes applied:**
- `jarvis/prompts/system.md`: Added `## CRITICAL: ASCII-only in code and tool arguments`. All Python variable names, comments, and string literals must be ASCII. Replacements: `ρ`→`rho`, `φ`→`phi`, `μ`→`mu`, `−`→`-`.
- `jarvis/agent.py`: Added `tool_use_failed` / HTTP 400 catch in `chat()`. On match, retries once with an ASCII reminder prepended to the user message.

---

### build_cloud_model() bug fixed

`build_cloud_model(model_id, settings)` was ignoring the `model_id` parameter for Gemini — always used `settings.effective_cloud_model` for both primary and fallback. This meant fallback was always same model as primary.

**Fix in `jarvis/utils.py`:**
```python
gemini_model_id = model_id or settings.effective_cloud_model
return GeminiModel(gemini_model_id, provider=GoogleGLAProvider(...))
```
All 4 sub-agents updated to use `build_cloud_model()`.

---

### Gemini free-tier quota burn-through

- `gemini-2.5-flash` (50 RPD): exhausted after first HW5 attempt.
- `gemini-2.0-flash` (200 RPD): exhausted during Groq investigation runs.
- **Primary model finalized:** `gemini-2.5-flash-lite` (1000 RPD). Updated `.env` + `config.py`.

Quotas reset at midnight UTC.

---

### HW5 end-to-end pipeline completed ✅

**Task:** PET 212E HW-5 — petroleum engineering data analysis (porosity, permeability, Klinkenberg effect).

**Tool call sequence:**
1. `pdf_read` — assignment PDF
2. `excel_read` — `PET 212E_HW-5.xlsx` (absolute Desktop path)
3. `generate_code` → CoderAgent wrote `plot.py` (4 matplotlib plots + scipy trendlines)
4. `file_write` — saved `vault/reports/HW5/plot.py`
5. `python_run` — produced `plot_a.png`, `plot_b.png`, `plot_c.png`, `plot_d.png`
6. `file_list` — confirmed PNGs
7. `report_write` + `report_compile` — LaTeX → pdflatex

**Output:** `vault/reports/HW5/HW5_Report.pdf` — 272 KB, 5 pages. 4 embedded plots; porosity vs grain density, semi-log permeability vs He-porosity (R²=0.91), He vs atmospheric porosity (R²=0.99), Klinkenberg log-log analysis.

---

### Excel header auto-detection improved

**Problem:** HW5 file structure — Row 0 = title "ITU-PDGM Well-1", Row 1 = column headers, Row 2 = units row, Row 3+ = data. Model guessed `skiprows=3` → wrong column names → `KeyError`.

**Fix in `jarvis/tools/excel.py`:**
- `_find_header_row()`: row with most non-null string values = header. Title row (1 string) loses to actual header row (16 strings).
- Units row auto-detected and added to `rows_to_skip`.
- Output includes `Recommended pandas read: pd.read_excel(path, skiprows=[...], header=0)` so model copies it directly.

---

### `excel_read` and `python_run` tools added

- `jarvis/tools/excel.py` — pandas-based Excel reader with header auto-detection.
- `jarvis/tools/python_exec.py` — subprocess `.py` runner, 60s timeout, 4000 char cap.
- Registered in `jarvis/agent.py` → total **15 tools**.
- System prompt updated with tool descriptions + data→plot→LaTeX workflow.
- Dependencies added: `pandas`, `openpyxl`, `matplotlib`, `scipy`.

---

### /model command + natural language model switching

**`AVAILABLE_MODELS`** (8 models): 4 Gemini + 4 Groq, module-level constant in `agent.py`.

**`switch_model(model_id)`**: builds new model at runtime, replaces `_cloud_primary`, tracks `_active_model_id` for label display. Raises `ValueError` if Groq key missing.

**`/model` command in `cli.py`:**
- `/model` → Rich Table (numbered, ★ active) → interactive number/keyword prompt
- `/model 3` or `/model flash` → inline selection, no prompt

**Natural language detection (`_detect_model_switch`):**
- 10 regex patterns (Turkish + English)
- `_MODEL_KEYWORDS` lookup: longer/specific first to avoid "flash-lite" matching "flash" first
- Short-sentence fallback (≤8 words) for bare keywords like "pro'ya geç"
- 18/18 test cases passing. Voice mode speaks confirmation aloud.

---

### ChromaDB reset

Deleted `data/chroma/` — 128 stale test entries were injecting 2000+ tokens of irrelevant HW5 memory into new queries.

---

### ProjectState.md overhauled

Complete rewrite: Iteration 3 complete, all 15 tools, updated architecture, key decisions, known issues, full file map, roadmap.

---

## 2026-05-24 — log.md frozen; see ProjectState.md and CHANGELOG.md

Faz 4 through Faz 21 are complete (35 tools, LangGraph orchestration, FastAPI, Electron HUD,
Flutter Android app, Kotlin wakeword service). The detailed state is in ProjectState.md, which
is kept current. This file has not been updated since 2026-05-09 and is now frozen.

Going forward: use CHANGELOG.md for notable additions and ProjectState.md for current architecture.

See docs/ARCHITECTURE.md for a subsystem map and the phased refactor roadmap in the Claude plan file.
