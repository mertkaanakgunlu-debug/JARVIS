# ProjectState.md
> **Read this first at the start of every new Claude Code session.**
> Update this file after every meaningful change.

## Current Iteration: 3 — Orchestrator + Tools + Voice (COMPLETE)

**Started:** 2026-05-08
**Completed:** 2026-05-09

---

## Status: 🟢 Fully Operational

### Done ✅

#### Iteration 1 — Text CLI MVP (2026-05-08)
- [x] Full codebase scaffolded: config, agent, memory, cli, tools, prompts
- [x] Ollama + Qwen 2.5 7B running locally
- [x] ChromaDB embeddings (nomic-embed-text via Ollama)
- [x] Gemini API wired (flash + pro tiers)
- [x] pydantic-ai v1 migration complete
- [x] Rich REPL with gold/blue theme + JARVIS banner

#### Iteration 2 — Orchestrator + Sub-agents (2026-05-08/09)
- [x] 4 sub-agents: math_solve, write_content, research, generate_code
- [x] Direct tools: shell_run, file_read, file_write, file_list, note_append, pdf_read, web_search (Tavily), report_write, report_compile
- [x] LaTeX → PDF pipeline via pdflatex (MiKTeX)
- [x] Retry logic: run_with_retry() for 429/503; daily-quota detection → auto-fallback
- [x] Cloud-only orchestration (local Qwen retained only as degraded emergency fallback)

#### Iteration 3 — Voice I/O (2026-05-08)
- [x] STT: Faster-Whisper large-v3-turbo (GPU, auto-downloads ~800 MB)
- [x] TTS: edge-tts v7.x — en-US-ChristopherNeural, tr-TR-AhmetNeural
- [x] VAD: energy-based silence detection (1.5 s configurable)
- [x] Quasi-live streaming: chat_stream() → sentence-chunked TTS queue
- [x] Language auto-detection; Turkish → system prompt injection
- [x] Exit phrases: English + Turkish

#### Major fixes & additions (2026-05-09)
- [x] **File access widened:** `_resolve()` in files.py now allows absolute paths within `~` (home dir). User files on Desktop/Documents/OneDrive now accessible.
- [x] **Excel reader:** `jarvis/tools/excel.py` — pandas-based, auto-detects header row, emits correct `skiprows` hint. Replaces manual guessing.
- [x] **Python script runner:** `jarvis/tools/python_exec.py` — subprocess with 60 s timeout; used for plot generation.
- [x] **2 new tools registered:** `excel_read` and `python_run` in agent.py (now 15 tools total).
- [x] **System prompt updated:** new tools documented, data → plot → LaTeX workflow, ASCII-only mandate for code args.
- [x] **Dependencies added:** pandas, openpyxl, matplotlib, scipy.
- [x] **build_cloud_model() utility:** unified Groq/Gemini model factory in utils.py; all 4 sub-agents use it.
- [x] **Groq integration (optional):** GROQ_API_KEY in .env activates Groq provider. Currently commented out (free-tier TPM limits too low for complex orchestration with 5400+ token prompt). Key preserved for simple/voice path if needed.
- [x] **Gemini fallback bug fixed:** `build_cloud_model()` was ignoring `model_id` for Gemini — now passes it correctly.
- [x] **tool_use_failed retry:** 400 tool-use-failed errors (Groq Unicode issue) now caught and retried with ASCII reminder.
- [x] **HW5 completed end-to-end:** pdf_read → excel_read → Python plot script → 4 PNGs → LaTeX report → 272 KB 5-page PDF at `vault/reports/HW5/HW5_Report.pdf`.
- [x] **`/model` command:** numbered menu, numara veya isimle seçim, runtime model switch via `switch_model()`.
- [x] **Natural language model switching:** Türkçe/İngilizce "Modeli flash yap", "Switch to pro" etc. — 18/18 test geçti.
- [x] **AVAILABLE_MODELS catalogue:** 8 models (4 Gemini, 4 Groq) in agent.py with TPM/RPD info.

### In Progress 🔄
*(none)*

### Blocked ⛔
*(none)*

### Up Next ⬜
- **Iteration 4:** Wake-word via Porcupine ("Hey JARVIS") — always-listening loop
- **Iteration 5:** FastAPI server + PWA over Tailscale (phone access)
- **Iteration 6:** WoL, Spotify, calendar/email read-only, Word/PPT generation

---

## Active Configuration (.env)

```
GEMINI_API_KEY=AIza...        ← primary orchestrator
TAVILY_API_KEY=tvly-dev-...   ← web search
#GROQ_API_KEY=gsk_...         ← commented out; activate for Groq provider
GROQ_MODEL=meta-llama/llama-4-scout-17b-16e-instruct
GROQ_MODEL_FALLBACK=llama-3.3-70b-versatile
CLOUD_MODEL=gemini-2.5-flash-lite   ← primary (1000 RPD free)
CLOUD_MODEL_PRO=gemini-2.5-pro      ← pro tier override
CLOUD_TIER=flash
ESCALATION_WORD_THRESHOLD=50
LOCAL_MODEL=qwen2.5:7b-instruct
EMBED_MODEL=nomic-embed-text
USER_NAME=Sir
```

---

## Key Decisions

| Decision | Choice | Reason | Date |
|---|---|---|---|
| LLM backend | Cloud-only orchestrator | Qwen 2.5 7B too weak for sub-agent delegation | 2026-05-08 |
| Cloud primary | `gemini-2.5-flash-lite` | 1000 RPD free tier; 2.0-flash (200 RPD) and 2.5-flash (50 RPD) exhausted in testing | 2026-05-09 |
| Cloud fallback | `gemini-2.5-flash-lite` | Same model — if primary exhausted, fallback is same model (low RPD risk) | 2026-05-09 |
| Groq | Commented out (optional) | Free-tier TPM ≤ 12k; system prompt alone is ~5400 tokens; no headroom for multi-step tool calling | 2026-05-09 |
| Agent framework | Pydantic-AI v1.x | Dual-backend, validated tool calls | 2026-05-08 |
| Memory recall | n=2 (was n=5) | Reduces prompt tokens; important when Groq is active | 2026-05-09 |
| max_tokens | 4000 | Enough for tool-call JSON; prevents TPM overflow on Groq | 2026-05-09 |
| File access | workspace OR home (~) | Desktop/OneDrive files must be accessible | 2026-05-09 |
| Excel header detection | Max string-count heuristic | First row with most non-null string values = header | 2026-05-09 |
| ASCII mandate | In system prompt | Groq rejects Unicode in function call JSON | 2026-05-09 |
| Vector DB | ChromaDB (local) | Zero-cost, offline | 2026-05-08 |
| Vault format | Obsidian Markdown | Human-readable, future-proof | 2026-05-08 |

---

## Architecture (Current)

```
CLI (Rich)
  │
  ├─ /model → switch_model() runtime model change (8 models in catalogue)
  ├─ natural language "modeli flash yap" → auto-detected, no LLM call needed
  │
  └─ JarvisAgent.chat() / chat_stream()
       │
       ├─ _cloud_primary  → gemini-2.5-flash-lite  (default)
       ├─ _cloud_fallback → gemini-2.5-flash-lite  (daily quota hit)
       └─ _local_model    → qwen2.5:7b-instruct    (503/UNAVAILABLE emergency only)
            │
            └─ 15 registered tools:
                 Direct: shell_run, file_read, file_write, file_list,
                         note_append, pdf_read, excel_read, python_run,
                         web_search, report_write, report_compile
                 Sub-agents: math_solve, write_content, research, generate_code
                    └─ All sub-agents use build_cloud_model() → same Gemini/Groq
```

---

## Tool Reference (15 tools)

| Tool | What it does |
|---|---|
| `shell_run(cmd)` | PowerShell command (safe deny-list) |
| `file_read(path)` | Read text file (absolute or workspace-relative) |
| `file_write(path, content)` | Write file (creates parents) |
| `file_list(path)` | List directory |
| `note_append(topic, body)` | Save to vault/notes/ |
| `pdf_read(path)` | Extract text from PDF |
| `excel_read(path, sheet?)` | Read .xlsx — auto-detects header, emits skiprows hint |
| `python_run(script_path)` | Execute .py script (60 s timeout) |
| `web_search(query)` | Tavily quick lookup |
| `report_write(title, body)` | Write LaTeX .tex to vault/reports/ |
| `report_compile(tex_path)` | pdflatex → PDF (MiKTeX) |
| `math_solve(problem)` | MathAgent sub-agent → LaTeX |
| `write_content(topic, style)` | WriterAgent → LaTeX prose |
| `research(query)` | ResearchAgent → web-augmented LaTeX |
| `generate_code(spec)` | CoderAgent → Python/LaTeX |

---

## File Map

| File | Purpose |
|---|---|
| `jarvis/config.py` | Settings (pydantic-settings + .env); AVAILABLE_MODELS-aware label |
| `jarvis/agent.py` | Orchestrator agent; AVAILABLE_MODELS, switch_model(), build_agent() |
| `jarvis/memory.py` | ChromaDB recall (n=2) + vault markdown writer |
| `jarvis/cli.py` | Rich REPL; /model menu + NL switch detection; /recall, /status, /help |
| `jarvis/utils.py` | build_cloud_model(), is_daily_quota_error(), run_with_retry() |
| `jarvis/__main__.py` | Entry point (`python -m jarvis`, `--voice` flag) |
| `jarvis/tools/shell.py` | Shell exec with deny-list |
| `jarvis/tools/files.py` | file_read/write/list — allows ~/ paths |
| `jarvis/tools/notes.py` | vault/notes/ appender |
| `jarvis/tools/pdf.py` | pdfplumber text extraction |
| `jarvis/tools/excel.py` | pandas Excel reader with header auto-detection |
| `jarvis/tools/python_exec.py` | subprocess .py runner with timeout |
| `jarvis/tools/web.py` | Tavily search |
| `jarvis/tools/latex.py` | latex_write() + latex_compile() |
| `jarvis/subagents/math.py` | MathAgent (uses build_cloud_model) |
| `jarvis/subagents/writer.py` | WriterAgent (uses build_cloud_model) |
| `jarvis/subagents/research.py` | ResearchAgent (uses build_cloud_model) |
| `jarvis/subagents/coder.py` | CoderAgent (uses build_cloud_model) |
| `jarvis/voice.py` | VoiceEngine (STT + TTS + VAD) |
| `jarvis/prompts/system.md` | JARVIS system prompt (includes ASCII mandate, workflow) |
| `.env` | API keys, model names, thresholds |

---

## Known Issues / Gotchas

1. **Gemini free-tier RPD**: `gemini-2.5-flash-lite` = 1000 RPD; `gemini-2.5-flash` = 50 RPD; `gemini-2.0-flash` = 200 RPD. Development testing burned through 2.5-flash and 2.0-flash quotas on 2026-05-09. Quotas reset at midnight UTC.
2. **Groq TPM**: System prompt + tool schemas = ~5400 input tokens. Free-tier limits: qwen/qwen3-32b = 6k TPM (insufficient), llama-3.3-70b = 12k TPM (hallucinates tool calls), llama-4-scout = 30k TPM (too small model, refuses complex tasks). Groq only viable for simple 1-step queries.
3. **Excel skiprows**: The improved `excel_read` auto-detects header row and emits the correct `skiprows` parameter. Always trust this output when writing plot scripts.
4. **ChromaDB under OneDrive**: may cause sync churn. `data/chroma/` is gitignored. Move `CHROMA_DIR` outside OneDrive in `.env` if sync becomes noisy.
5. **pydantic-ai v1**: use `output_type=`, `result.output`, `OpenAIProvider`, `GoogleGLAProvider`. Old docs use `result_type=`, `result.data` — wrong.
6. **tool_use_failed (Groq)**: Groq rejects Unicode characters in function call JSON. ASCII-only mandate in system prompt addresses this. Also caught and retried in agent.chat().
7. **MiKTeX path**: `C:\Users\mertk\AppData\Local\Programs\MiKTeX\miktex\bin\x64\pdflatex.EXE`

---

## Next Session Checklist
1. Read this file
2. Check `log.md` for last session's decisions
3. Verify Ollama running: `ollama ps`
4. Activate venv: `.\.venv\Scripts\Activate.ps1`
5. Run JARVIS: `python -m jarvis`

---

## Roadmap

| # | Name | Status |
|---|---|---|
| 1 | Text CLI MVP | ✅ Complete |
| 2 | Orchestrator + Sub-agents + LaTeX | ✅ Complete |
| 3 | Voice I/O (Whisper + edge-tts) | ✅ Complete |
| 4 | **Wake-word** (Porcupine "Hey JARVIS") | ⬜ Next |
| 5 | FastAPI server + PWA over Tailscale | ⬜ |
| 6 | WoL, Spotify, calendar/email, Word/PPT | ⬜ |
