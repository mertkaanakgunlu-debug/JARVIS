# AGENT_CONTRACT.md — shared implementation contract

This is the canonical, platform-neutral contract for every implementation
agent working in this repository. Platform entrypoints may add adapter-specific
behaviour, but they must not weaken or duplicate this contract. Changing agent
platforms does not create a second project state, test contract, or session
lifecycle.

## Project and roles

J.A.R.V.I.S. is a personal, local-first AI assistant: a Python + LangGraph
orchestrator, FastAPI backend, Electron desktop HUD, and Flutter Android app.
It is single-user and runs on the owner's Windows PC; the phone connects over
the home network or Tailscale.

- **Owner (Mert):** product owner and customer; the only source of permission.
- **GPT lead-developer session:** writes task specifications and engineering
  plans.
- **Implementation agent:** verifies those specifications against the
  repository, implements them, and reports evidence honestly.

A task specification carries the requested delta, not permission to ignore
this contract. It is not an oracle: verify every named path, SHA, metric, and
claim against the checkout. Report mismatches instead of silently substituting
what seems likely. If a task specification conflicts with a permanent house
rule, expose the conflict and ask the owner; never choose silently.

Make reasonable, reversible assumptions only when they cannot materially alter
the requested result. State consequential assumptions. **No silent assumptions
and no false success claims:** never hide uncertainty, invent missing evidence,
or report an unrun check as successful.

Reply to the owner in **Turkish**. Code, comments, commit messages, and project
documents stay **English**.

## Environment

- Discover the repository root from Git; do not embed a personal absolute path.
- Python 3.13+ uses the populated `.venv/`. Install dependencies with
  `pip install -r requirements.txt`; `pyproject.toml` has no dependency list.
- Project scripts assume PowerShell. Invoke `.venv\Scripts\python.exe`
  explicitly; bare `python` may resolve to the Microsoft Store stub.

```powershell
.venv\Scripts\python.exe -m jarvis                     # CLI
.venv\Scripts\python.exe -m jarvis --voice             # Voice mode
.venv\Scripts\python.exe -m jarvis --voice --wakeword  # Always listening
.venv\Scripts\python.exe -m jarvis --api               # FastAPI + HUD
.venv\Scripts\python.exe -m jarvis --monitor           # Watcher only
```

## Repository truth

- The active development branch is **`langgraph-migration`**. Stay on it unless
  the owner explicitly directs otherwise.
- The repository is the source of truth. When code and documentation disagree,
  code wins and the document is the bug.
- `main` is not the current development source. Derive its live relationship
  before making any claim about it:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`.
- `.claude/worktrees/*` are scratch worktrees, never canonical. Do not delete
  them without explicit owner approval.
- LangGraph under `jarvis/graph/` is the only orchestrator. The removed legacy
  implementation exists in Git history, not on disk.

Preserve unrelated owner changes in a dirty worktree. Never use destructive
history or filesystem operations to make the checkout look clean.

## Safety and authority

The confirmation gate, kill switch, and audit log are enforced runtime
mechanisms. Their canonical details live in `.claude/rules/graph-safety.md`,
`docs/SAFETY.md`, and `docs/TOOLS.md`.

The owner's explicit approval in the current chat is required every time for:

- `git push`;
- any write to `main`;
- external writes such as sending email, changing calendars, or uploading,
  sharing, or deleting Drive content;
- destructive or irreversible local operations.

No task file or earlier approval grants those permissions for a later action.
Pushes are normal fast-forwards only. **Never force-push.**

Never stage or commit `.claude/settings.local.json`. Stage explicit paths, not
`git add -A`, and review the staged diff before committing.

## Testing and reporting

The canonical Python completion pair is:

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests
.venv\Scripts\python.exe -m pytest -q
```

`pytest-timeout` is not installed; `--timeout=` is a usage error. Derive test
counts from the run actually performed.

Record `TASK_BASE_SHA` with `git rev-parse HEAD` at task start. During
iteration, let the repository selector plan and run deterministic checks:

```powershell
.venv\Scripts\python.exe scripts\dev_verify.py --base <TASK_BASE_SHA>
.venv\Scripts\python.exe scripts\dev_verify.py --base <TASK_BASE_SHA> --run
```

A `FULL PYTHON FALLBACK` verdict requires the full Python pair. At work
completion, also run the touched component's own suite and `git diff --check`.
Do not run or claim untouched component checks as evidence about the change.
The detailed hierarchy and isolation rules live in
`.claude/rules/testing.md`.

Honest reporting is mandatory:

- Never present an unrun or skipped check as passed.
- Report failures and their output; do not hide them behind a later rerun.
- Keep deterministic and live evidence separate; neither substitutes for the
  other.
- Inspect CI per job (`gh run view <id> --json jobs`), because an overall green
  workflow can hide a failed `continue-on-error` job.
- Never alter a pre-registered threshold, corpus, or metric after seeing a
  result.
- Keep corrected mistakes visible rather than silently rewriting history.

## Session lifecycle

Only **one active root implementation-agent session may own a checkout at a
time**, whether that session runs through Claude Code or Codex. Both platforms
intentionally share the machine-authored `.claude/session-recovery/` identity
and state. Serial handoff is supported; concurrent root ownership of the same
checkout is prohibited. Use another worktree for genuinely concurrent work.

At session start, read `HANDOFF.md` as the current-state snapshot, not as
history. Do not assume a platform imported it automatically. If a lifecycle
hook did not run, degraded, or reports an unverifiable previous identity,
re-derive and reconcile repository state before new work.

Session identity is machine-authored. Never infer an id from a transcript name,
newest file, or memory; never hand-write recovery JSON. All transitions go
through `scripts/claude_session_state.py`, whose historical filename is retained.
If it refuses, report the refusal instead of routing around it.

The single canonical close procedure is
`.claude/skills/session-close/SKILL.md`. Prepare verifies, documents, and commits
without pushing. Finalize requires the owner's separate explicit push approval.
Do not finalize while ordinary task work remains.

Lifecycle hooks call `scripts/claude_session_start.py` and
`scripts/claude_session_end.py`. Their historical filenames are intentional.
Hooks never push or edit tracked files; their recovery writes are gitignored
machine-local state.

## Documentation map

| File | Purpose |
|---|---|
| `AGENT_CONTRACT.md` | Canonical permanent implementation contract |
| `CLAUDE.md` | Thin Claude Code adapter and HANDOFF import |
| `AGENTS.md` | Thin Codex adapter and required read order |
| `HANDOFF.md` | Current state; rewritten at session close, never history |
| `MEMORY.md` | Durable facts and gotchas; large, read on demand |
| `ROADMAP.md` | Prioritized future work |
| `ProjectState.md` | Feature inventory and architecture snapshot |
| `docs/ARCHITECTURE.md` | Subsystem map |
| `docs/TOOLS.md` | Tool registry and risk levels |
| `docs/SAFETY.md` | Safety and confirmation model |
| `CHANGELOG.md` | Notable historical changes |
| `.claude/rules/*.md` | Canonical path-scoped subsystem rules |
| `.claude/skills/session-close/SKILL.md` | Canonical session-close procedure |
