# J.A.R.V.I.S. — Safety & Confirmation Model

> Current state (Faz 21). Phase 3 of the refactor roadmap will add a formal gate.

## Current safety mechanisms

| Mechanism | Where | What it covers |
|---|---|---|
| `DENY_PATTERNS` | `jarvis/tools/shell.py:9` | Blocks `rm -rf`, `format`, `del /f`, etc. in `shell_run` |
| System prompt rules | `jarvis/prompts/system.md` | "NEVER" directives (no hallucination, no fake data, etc.) |

There is no uniform confirmation gate. The assistant can currently send emails, create calendar
events, delete files, and run shell commands without user confirmation beyond what the LLM
chooses to ask for in the conversation.

`jarvis/tools/shell.py:38` has a `confirmed: bool = False` kwarg that is never set by any caller.
This is reserved as the hook for the Phase 3 gate.

## Planned: Phase 2 — ToolSpec metadata

A `ToolSpec` dataclass will annotate all 35 tools with:
- `risk_level`: L1 (read) / L2 (reversible local write) / L3 (external side-effect)
- `requires_confirmation`: bool override
- `side_effect_type`: enum (none / local / external / destructive)

## Planned: Phase 3 — Confirmation gate

A LangGraph `confirmation` node inserted between `agent` and `tools` will:
- Pause the graph via `interrupt()` for any L3 tool call
- Surface a structured confirmation request to all five interaction modes
  (CLI, API, WebSocket HUD, Flutter mobile, voice TTS)
- Resume via `Command(resume="approve"|"deny"|"edit")`
- Be gated behind `confirmation_gate_enabled: bool` (off by default initially)

Monitor / background task context will auto-deny L3 actions and queue them for user review.
