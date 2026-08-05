# CLAUDE.md — project operating contract

Permanent rules only. Anything that changes between sessions lives in
[HANDOFF.md](HANDOFF.md) (imported below), and anything scoped to a subtree lives
in `.claude/rules/*.md`, which load automatically when you touch matching files.

## What this is

J.A.R.V.I.S. — a personal, local-first AI assistant. Python + LangGraph
orchestrator, FastAPI backend, Electron desktop HUD, Flutter Android app. Single
user, runs on the owner's Windows PC; the phone reaches it over the home network
or Tailscale.

## Roles

- **Owner (Mert)** — product owner and customer. The only source of permission.
- **A GPT session** — lead developer. Writes the task specs.
- **Claude Code (you)** — implementer.

Work arrives as a markdown prompt file in `C:\Users\mertk\Desktop\GPT_Prompts\`
(e.g. `Pr_3.md`), referenced by the owner in chat. That file is the task
specification. Three consequences, all binding:

- **A spec is not an oracle.** It is written without the repo open, so it can
  name a path, SHA, metric or file that has moved or never existed. Verify every
  concrete claim against the code, and **report the mismatch** instead of
  silently substituting what you found.
- **Its constraints hold even when they cost effort** — commit splits, "do not
  touch main", "do not invent a threshold", "do not hide a failing job". Where a
  spec and this file disagree on a house rule, say so and ask; never pick
  silently.
- **A spec is not a permission grant.** Push, external writes and destructive
  operations need the owner's own go-ahead in chat, every time.

Reply to the owner in **Turkish**. Code, comments, commit messages and documents
stay **English**.

## Environment

- Repo root: `C:\Users\mertk\Desktop\Jarvis` — **not** under OneDrive.
- Python 3.13+, venv at `.venv/` (populated). Install via
  `pip install -r requirements.txt`; `pyproject.toml` carries no dependency list.
- Shell: this project's scripts assume **PowerShell**. Use the venv interpreter
  explicitly (`.venv\Scripts\python.exe`) — bare `python` on this machine
  resolves to the Microsoft Store stub and fails.

```powershell
python -m jarvis                     # CLI (Rich REPL)
python -m jarvis --voice             # Voice mode
python -m jarvis --voice --wakeword  # Always-listening "Hey JARVIS"
python -m jarvis --api               # FastAPI REST + WebSocket HUD (port 8000)
python -m jarvis --monitor           # Background watcher only
```

## Repository truth rules

- Active branch: **`langgraph-migration`**. Stay on it unless told otherwise.
  Both it and `main` push to `github.com/mertkaanakgunlu-debug/JARVIS`.
- `main` is **behind** and is a strict ancestor — catching it up is a pure
  fast-forward whenever the owner asks. Don't read `main` as current, and
  **never quote how far behind it is** — derive it:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`
- **The repository is the source of truth.** When a document and the code
  disagree, the code wins and the document is the bug.
- `.claude/worktrees/*` are scratch branches from past sessions — **never**
  canonical. Four remain because they hold commits unreachable from
  `langgraph-migration`; two may be worth recovering. Don't delete them without
  an explicit go-ahead.
- `jarvis/legacy/` (the old pydantic-ai orchestrator) was deleted, not archived.
  LangGraph (`jarvis/graph/`) has been the only orchestrator since the refactor;
  the old code is in git history, not on disk.

## Safety and push authority

The confirmation gate, the kill switch and the audit log are real and enforced —
you **can** tell the user a risky action will pause for confirmation, because it
does. Details, invariants and the honest list of known limits are in
`.claude/rules/graph-safety.md`, which loads when you touch the relevant files;
`docs/SAFETY.md` and `docs/TOOLS.md` have the full mechanism and risk tables.

Requires the owner's explicit in-chat approval, every time:

- `git push` (and it is **always** a normal fast-forward — no force-push, ever)
- any write to `main`
- external writes (email send, calendar create/delete, Drive upload/share/delete)
- destructive or irreversible local operations

Never stage or commit `.claude/settings.local.json`. Stage explicit paths rather
than `git add -A`, and review what is staged before committing.

## Test and reporting standard

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests
.venv\Scripts\python.exe -m pytest -q
```

`pytest-timeout` is not installed — `--timeout=` is a usage error. Test counts
are **derived from the run you did**, never quoted from a document. Details on
isolation fixtures and measurement discipline: `.claude/rules/testing.md`.

Honest reporting is not optional here:

- Never present an unrun check as passed or a skipped step as done.
- If tests fail, say so and show the output.
- Separate **deterministic** evidence from **live** evidence; neither substitutes
  for the other.
- Read CI **per job** (`gh run view <id> --json jobs`) — a green workflow hides
  failing `continue-on-error` jobs.
- Never change a pre-registered threshold, corpus or metric after seeing a
  result.
- Keep corrected mistakes visible rather than quietly rewriting them.

## Session protocol

**Start.** The SessionStart hook (`scripts/claude_session_start.py`) injects a
preflight block: branch, HEAD, upstream, ahead/behind, dirty files, `main`,
whether HANDOFF's verified SHA is an ancestor of HEAD, and whether the previous
session closed cleanly. It is best-effort and fail-open — if it says
`SESSION PREFLIGHT DEGRADED`, re-derive the state yourself before trusting any
claim. HANDOFF.md is imported below, so no orientation prompt is needed.

If the preflight reports the previous session did not close, reconcile that
before starting new work.

**Close.** Run `/session-close prepare`, then `/session-close finalize` only
after the owner approves the push. The skill
(`.claude/skills/session-close/SKILL.md`) owns the whole checklist. The
SessionEnd hook writes a local, gitignored recovery breadcrumb on every exit —
it never commits, pushes, or edits any tracked file, so an unexpected exit is
recoverable but never mistaken for a clean close.

Hooks never push and never write outside `.claude/session-recovery/`.

## Docs map

| File | What it's for |
|---|---|
| `CLAUDE.md` (this file) | Permanent operating contract |
| `HANDOFF.md` | Current state — imported below, rewritten each session close |
| `MEMORY.md` | Durable facts and gotchas. **Large (~74 KB) — read on demand, not preloaded**; grep it when a decision touches past incidents |
| `ROADMAP.md` | What's next, prioritized |
| `ProjectState.md` | Full feature inventory + architecture snapshot |
| `docs/ARCHITECTURE.md` | Subsystem map |
| `docs/TOOLS.md` | Tool registry with risk levels |
| `docs/SAFETY.md` | Safety/confirmation model |
| `CHANGELOG.md` | Notable changes |
| `.claude/rules/*.md` | Path-scoped rules, auto-loaded per subtree |

---

@HANDOFF.md
