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
  CONTRIBUTING.md's `cd` paths were fixed to match this during the GPT-5.6 review remediation
  pass, 2026-07-15 — no longer stale.)
- Python 3.13+, venv at `.venv/` (already populated — `.\.venv\Scripts\Activate.ps1`).
- Canonical install is `pip install -r requirements.txt` — `pyproject.toml` has no dependency list.
- Shell: this project's own scripts assume **PowerShell**, not bash.
- A pytest suite exists under `tests/` (added Faz 8, 2026-07-15; **559 tests as of 2026-07-21**,
  ~3 min offline) — run with `python -m pytest -q` from the repo root. Note `pytest-timeout` is
  NOT installed, so `--timeout=` is a usage error. Covers `policy_guard`, `session_store`
  concurrency, the provider router's offline-failover behavior, a regression test per Faz 8 bug
  fix, and (Agent Runtime rev.2) the execution-contract types, the shared redaction layer, the
  off-vs-shadow equivalence replay, and the A/B harness guards. Still not
  exhaustive — most tool modules still have no coverage; extend `tests/` rather than
  reintroducing ad-hoc throwaway scripts for anything that touches shared logic (safety
  kernel, stores, routing). **Before writing a test that constructs `SessionStore`,
  `UsageTracker`, `kill_switch`, or anything else that resolves paths as `Path("data")/...`
  relative to cwd, use the `isolated_cwd` fixture in `tests/conftest.py`** — see its
  docstring for why (MEMORY.md's isolate-test-data-paths incident).

## Running it

```powershell
python -m jarvis                    # CLI (Rich REPL)
python -m jarvis --voice             # Voice mode
python -m jarvis --voice --wakeword  # Always-listening "Hey JARVIS"
python -m jarvis --api               # FastAPI REST + WebSocket HUD (port 8000)
python -m jarvis --monitor           # Background watcher only
```

## Branch / source-of-truth conventions

- Active development branch: **`langgraph-migration`** — keep working on it unless told otherwise.
  Both it and `main` are pushed to `github.com/mertkaanakgunlu-debug/JARVIS`.
  **They have NOT pointed at the same commit since 2026-07-15** — as of 2026-07-21 `main` is
  **47 commits behind** `langgraph-migration` (everything from the two-metric oracle through
  Agent Runtime rev.2 Faz 1 and the A/B harness hardening lives only on `langgraph-migration`).
  `main` has no commits of its own, so catching it up is still a pure fast-forward whenever the
  owner wants one; nothing is lost meanwhile, but don't read `main` as current.
- `.claude/worktrees/*` are scratch branches from past Claude Code sessions — **never**
  treat them as canonical source. 17 of the original 21 were confirmed fully-merged into
  `langgraph-migration` (zero unique content) and deleted 2026-07-15. **4 remain**
  (`claude/eager-noether-46af01`, `claude/gifted-wilbur-e021ea`, `claude/stoic-spence-2c5246`,
  `claude/thirsty-mclean-f67665`) because they contain commits not reachable from
  `langgraph-migration` — two look superseded (an alternate subagent migration, an alternate
  Calendar integration) but two (`gifted-wilbur`'s eval regression suite, `stoic-spence`'s
  rolling/hierarchical summarization) don't have an obvious equivalent in the current codebase and
  may be worth recovering rather than deleting — see [HANDOFF.md](HANDOFF.md). Don't delete these
  four without another explicit go-ahead.
- `jarvis/legacy/` (the old pydantic-ai orchestrator) was retired in Faz 8 (2026-07-15) — deleted
  outright, not archived. LangGraph (`jarvis/graph/`) has been the only orchestrator since Faz 1
  of the refactor (2026-05-09); if you need the old implementation for reference, it's in git
  history before that commit, not on disk.

## Safety model — read before touching tool-calling code

**Faz 4 (2026-07-14) built the real safety kernel** — `jarvis/policy_guard.py`, gating through
`jarvis/graph/nodes.py`'s `make_confirmation_node`, wired into all three interfaces (CLI text,
CLI/API voice, API). Practical implication, inverted from before: **you CAN now tell the user a
risky action (email send, calendar create/delete, shell exec, `python_run`, Drive
upload/share/delete) will pause and ask for confirmation first — it actually does**, in every
mode, by default (`confirmation_gate_enabled=True`). Read-only actions (list/search/...) on the
gated tools do not interrupt (per-action, not per-tool). There's also a kill switch
(`jarvis/kill_switch.py`, `/killswitch` in the CLI) that hard-blocks L3 actions with no prompt at
all when tripped, and an append-only audit log (`jarvis/audit_log.py`,
`data/audit_log.jsonl`) recording every risk_level ≥ 2 call's decision and outcome.

**Faz 5 (2026-07-15) extended the same gate to MCP tools** — `jarvis/mcp_integration.py` connects
to external MCP servers (disabled by default; ships with Microsoft's Playwright MCP for real
browser automation, `MCP_PLAYWRIGHT_ENABLED=True` to turn it on) and registers a `ToolSpec` per
discovered tool into the exact same registry the native tools use — zero changes needed to
`policy_guard`/the audit log/the kill switch. Fail-closed: anything beyond pure page
inspection/navigation (click, type, fill a form, run JS, ...) requires confirmation by default,
same as `gmail send`.

**Faz 7 (2026-07-15) added a second entry point into the graph** — `JarvisAgent.proactive_turn()`,
called from `jarvis/monitor.py` for background-initiated (not user-typed) turns, e.g. "a new email
arrived, is this worth surfacing?" Runs the exact same gate, zero changes to `policy_guard`/audit
log/kill switch — same principle as Faz 5's MCP tools. Off by default
(`monitor_proactive_enabled=False`). It can never raise `ConfirmationRequired` the way `chat()`
does (no interactive channel exists for a background thread to answer one) — an L3 interrupt is
discarded and turned into a notification instead ("confirm-or-notify, not silent execution").
**Practical implication you should know before touching this**: a live verification run found that
a misjudging/hallucinating model CAN cause a silent **L2** side effect (e.g. an unwanted
`procedure_save`) during a proactive check, since L2 writes bypass the gate by design (only L3 is
gated) and normally that's fine because a human is present to notice — a background check has
nobody watching. This is mitigated (the system prompt now explicitly forbids mutating tool calls
during a proactive check) but not structurally closed — see `docs/SAFETY.md`'s "What Faz 7 changed"
before assuming proactive turns are as safe as interactive ones.

**What's still genuinely not done** (see `docs/SAFETY.md`'s "Known limits" for the full honest
list — don't oversell past this):
- No Electron/mobile UI renders a confirmation prompt from the API's structured response yet —
  only CLI text and voice actually complete the approve/deny round-trip end-to-end today.
- `python_run`'s L2→L3 reclassification is an access-control fix, not a sandbox — the subprocess
  itself still has no resource/network restrictions.
- A background `TaskExecutor` job that hits a confirmable action fails with a clear message
  (there's no channel for it to ask) rather than actually resolving the confirmation.
- Proactive turns (Faz 7) only structurally gate L3 actions the same as any turn — the L2
  mitigation above is a prompt instruction on a non-deterministic model, not a hard guarantee.

See `docs/SAFETY.md` for the full mechanism list and `docs/TOOLS.md` for per-tool risk levels.

## Docs map

| File | What it's for |
|---|---|
| `CLAUDE.md` (this file) | Orientation + conventions for Claude Code sessions |
| `HANDOFF.md` | Where the *last* session left off — read every time you resume |
| `MEMORY.md` | Durable facts/decisions/gotchas that rarely change |
| `ROADMAP.md` | What's next, prioritized — Phase 6-8 + the current bug backlog |
| `ProjectState.md` | Full feature inventory (Faz 1-21) + architecture snapshot |
| `docs/ARCHITECTURE.md` | Subsystem map |
| `docs/TOOLS.md` | Tool registry with risk levels |
| `docs/SAFETY.md` | Safety/confirmation model (see caveat above — trust the code over this doc until it's re-verified) |
| `CHANGELOG.md` | Notable changes from Faz 4 onward |

## When you finish a session

Update [HANDOFF.md](HANDOFF.md) with what changed and what's next — that's the file a new
session reads first to avoid re-discovering context that's already been established.
