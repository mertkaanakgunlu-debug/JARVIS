# CLAUDE.md — Working notes for Claude Code in this repo

> Read this, then [HANDOFF.md](HANDOFF.md) (where the last session left off),
> then [MEMORY.md](MEMORY.md) (durable facts/gotchas). [ProjectState.md](ProjectState.md)
> has the full feature inventory; [ROADMAP.md](ROADMAP.md) has what's next.

## What this is

J.A.R.V.I.S. — a personal, local-first AI assistant. Python + LangGraph orchestrator,
FastAPI backend, Electron desktop HUD, Flutter Android app. Single user, runs on the
owner's Windows PC, phone talks to it over the home network / Tailscale.

## Project location & environment

- Repo root: `C:\Users\mertk\Desktop\Jarvis` — **not** under OneDrive. (README.md and
  CONTRIBUTING.md currently say to `cd` into a OneDrive path — that's stale, ignore it.)
- Python 3.13+, venv at `.venv/` (already populated — `.\.venv\Scripts\Activate.ps1`).
- Canonical install is `pip install -r requirements.txt` — `pyproject.toml` has no dependency list.
- Shell: this project's own scripts assume **PowerShell**, not bash.
- No test suite exists anywhere in `jarvis/` (`CONTRIBUTING.md` implies one should be run
  before committing — there's nothing to run yet).

## Running it

```powershell
python -m jarvis                    # CLI (Rich REPL)
python -m jarvis --voice             # Voice mode
python -m jarvis --voice --wakeword  # Always-listening "Hey JARVIS"
python -m jarvis --api               # FastAPI REST + WebSocket HUD (port 8000)
python -m jarvis --monitor           # Background watcher only
```

## Branch / source-of-truth conventions

- Active development branch: **`langgraph-migration`** (not yet merged to `main`).
- `.claude/worktrees/*` are scratch branches from past Claude Code sessions — **never**
  treat them as canonical source. There are currently 21 of them sitting on disk
  (see [HANDOFF.md](HANDOFF.md) — cleanup needs your explicit go-ahead, not done automatically).
- `jarvis/legacy/` is the old pydantic-ai orchestrator, kept for reference only. It is not
  imported by any live code path today — verify with a grep before assuming otherwise if
  you touch anything sub-agent-related (Phase 8 in [ROADMAP.md](ROADMAP.md) is the plan to
  finally retire it).

## Safety model — read before touching tool-calling code

A "Phase 3 confirmation gate" exists in `jarvis/graph/nodes.py` and is documented in
`docs/SAFETY.md` / `docs/TOOLS.md` — **but a full review (2026-07-14) found it does not
currently protect anything in practice**:

- `confirmation_gate_enabled` defaults to `False` in `jarvis/config.py`.
- Even when enabled, the CLI text REPL and voice loop (`jarvis/cli.py`, `jarvis/voice_api.py`)
  never handle the `ConfirmationRequired` exception / `__jarvis_confirm__` stream marker —
  only the FastAPI `/chat/confirm` path actually resumes a gated call.
- The system prompt (`jarvis/prompts/core/02_tool_policy.md`) explicitly tells the model
  "you do NOT need to ask" before any tool call, on the assumption the graph-level gate
  covers it.
- `python_run` executes arbitrary absolute-path Python with no sandboxing, but is classified
  low-risk (L2, no confirmation) — more powerful than `shell_run` (L3, deny-listed, gated).

**Practical implication:** don't tell the user (or assume) that a risky action "will ask for
confirmation first" — today, in the CLI/voice modes, it won't; it will either silently
execute or silently hang. See [ROADMAP.md](ROADMAP.md) P0 items before relying on this gate,
and see the full bug list from the 2026-07-14 review for exact file/line citations.

## Docs map

| File | What it's for |
|---|---|
| `CLAUDE.md` (this file) | Orientation + conventions for Claude Code sessions |
| `HANDOFF.md` | Where the *last* session left off — read every time you resume |
| `MEMORY.md` | Durable facts/decisions/gotchas that rarely change |
| `ROADMAP.md` | What's next, prioritized — Phase 5-8 + the current bug backlog |
| `ProjectState.md` | Full feature inventory (Faz 1-21) + architecture snapshot |
| `docs/ARCHITECTURE.md` | Subsystem map |
| `docs/TOOLS.md` | Tool registry with risk levels |
| `docs/SAFETY.md` | Safety/confirmation model (see caveat above — trust the code over this doc until it's re-verified) |
| `CHANGELOG.md` | Notable changes from Faz 4 onward |

## When you finish a session

Update [HANDOFF.md](HANDOFF.md) with what changed and what's next — that's the file a new
session reads first to avoid re-discovering context that's already been established.
