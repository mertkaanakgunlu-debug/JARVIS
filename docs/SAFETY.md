# J.A.R.V.I.S. — Safety & Confirmation Model

> Updated 2026-07-14. Phase 2 (ToolSpec metadata) and Phase 3 (confirmation gate) have
> **shipped** (commits `166a205`, `7e7e471`, 2026-05-24) — but a full review found the gate
> does not yet protect anything in practice. See the caveat below and
> [ROADMAP.md](../ROADMAP.md) P0 for the fix list.

## Current safety mechanisms

| Mechanism | Where | What it covers |
|---|---|---|
| `DENY_PATTERNS` | `jarvis/tools/shell.py:9` | Blocks `rm -rf`, `format`, `del /f`, etc. in `shell_run` |
| System prompt rules | `jarvis/prompts/core/*.md` (see [prompt_loader.py](../jarvis/prompts/prompt_loader.py)) | "NEVER" directives (no hallucination, no fake data, etc.) — **not** `jarvis/prompts/system.md`, which is a dead pointer file left over from before the Phase 1 prompt-modularization split |
| `ToolSpec` risk metadata | `jarvis/tool_registry.py` | `risk_level` (L1-L3) + `requires_confirmation` per tool |
| Confirmation gate | `jarvis/graph/nodes.py` (`make_confirmation_node`) | Interrupts the graph before tool calls where `requires_confirmation=True` |
| Meta-memory write-guard (Faz 2) | `jarvis/tools/files.py` (`PROTECTED_WRITE_PREFIXES`, checked in `write()`) | Unconditional, always-on — unlike the confirmation gate below, does **not** depend on `confirmation_gate_enabled` or which interface is running. `file_write` raises `PermissionError` for any path under `jarvis/prompts/core/` (persona/safety directives), regardless of what the agent is instructed to do. Read access is unaffected — only writes are blocked. |

## Known gap: the gate does not currently protect anything end-to-end

- `confirmation_gate_enabled` defaults to **`False`** in `jarvis/config.py` — off unless
  explicitly turned on.
- Even when enabled, only the FastAPI path (`POST /chat/confirm/{conf_id}`, `jarvis/api.py`)
  actually resumes an interrupted call. The CLI text REPL and voice loop
  (`jarvis/cli.py`, `jarvis/voice_api.py`) have no handling for the `ConfirmationRequired`
  exception or the `__jarvis_confirm__` stream marker — a gated call just hangs (text mode)
  or gets spoken/broadcast as raw JSON (voice/HUD mode).
- The system prompt (`jarvis/prompts/core/02_tool_policy.md`) tells the model it never needs
  to ask before calling a tool, on the assumption the graph-level gate covers it — so when
  the gate is off (the default) or not wired into the active interface, nothing asks at all.
- Gating is per-*tool*, not per-*action*: `google_calendar`/`gmail`/`google_drive`/`itu_mail`
  are each gated as a whole, so read-only actions (list/search) trigger the same interrupt
  as destructive ones (create/delete/send).
- `python_run` (arbitrary, unsandboxed Python execution from any absolute path) is classified
  L2/no-confirmation — more powerful than `shell_run`, which is L3/deny-listed/gated.

Treat the confirmation gate as **not yet a working safety net** until the CLI/voice wiring
and default-enabled question in [ROADMAP.md](../ROADMAP.md) P0 are resolved.

Monitor / background task context auto-denies L3 actions and queues them for user review
(this part is implemented as designed).
