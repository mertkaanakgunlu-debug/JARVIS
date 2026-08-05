---
name: session-close
description: Close a JARVIS working session — verify state, collect real test/CI evidence, rewrite HANDOFF.md, prepare the closing commit (prepare), and push it only after explicit owner approval (finalize). Use when the user says /session-close, asks to close/wrap up the session, or asks to hand off to the next session.
---

# Session close

Two modes, both idempotent — running either twice must not double-commit,
double-push, or corrupt the recovery marker.

```
/session-close prepare     # everything up to (not including) push
/session-close finalize    # push + verify, ONLY with explicit approval
```

If the user gave no mode, default to `prepare` and say so. `finalize` is never
implied by `prepare` succeeding.

Reply to the owner in Turkish; the documents and commit messages stay English.

---

## Mode: prepare

Stop and report at the **first** blocking condition rather than pushing through.

### 1. The session must actually be finished

- Every task in the task list is `completed` (or explicitly abandoned by the
  user, with that stated).
- **No background task is still running.** A running background task blocks the
  close — its result could change what the handoff says. Report which one and
  wait.
- No unanswered question is pending with the owner.

### 2. Verify state from the repository, never from memory

Re-derive all of it, this turn:

```bash
git status --porcelain=v1 -b
git rev-parse HEAD
git rev-parse --abbrev-ref HEAD
git fetch origin && git rev-list --left-right --count origin/<branch>...HEAD
git log --oneline -5
git log --format="%H <- %P" -3     # parent chain
```

Confirm the branch is the intended one, that `main` is untouched, and that the
commit chain is what the session actually intended to build.

### 3. Collect evidence that exists

Gather only what was **genuinely run in this session**, each bound to its exact
command and date:

- test runs (`pytest -q` output — the real counts)
- lint runs (`ruff check …`)
- CI results **at job level** (`gh run view <id> --json jobs`), Python /
  Electron / Mobile reported separately; first-run vs. rerun kept visible
- any live measurement, with n and whether the data was synthetic

**Never write an unrun check as passed.** If something was not run, the handoff
says it was not run. A test claim with no command and date behind it does not go
in.

### 4. Rewrite HANDOFF.md as current state

Overwrite it — HANDOFF is a snapshot, not a log. Sections:

1. Current verified state
2. Last completed work
3. Operational modes and rollout decisions
4. Tests and CI
5. Known open issues
6. Next engineering priority
7. Human-required actions
8. Session recovery notes

Rules the file must obey (see `.claude/rules/documentation.md`):

- It must **not** contain its own closing commit's SHA, and must **not** predict
  its own push or CI outcome. Count the closing commit relationally.
- No fixed ahead/behind number stated as a durable fact — give the command.
- Every test claim carries its command and date.
- If it ever contradicts the repository, the repository wins — say so in the file.

### 5. Update the other documents only if they actually changed

- **MEMORY.md** — only when a *durable, reusable* technical lesson was learned.
  A session's events are not a memory; a rule that will change future behaviour
  is. If nothing qualifies, leave it alone and say so.
- **ROADMAP.md** — only if priority actually moved.
- Never touch product behaviour code in a closing commit.

### 6. Run what the closing docs need

Re-run the checks whose results the handoff asserts, so the file is true at the
moment it is written:

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests
.venv\Scripts\python.exe -m pytest -q
git diff --check
```

### 7. Stage and commit

- **Never stage `.claude/settings.local.json`.** Stage explicit paths; do not
  use a blanket `git add -A`.
- Review what is staged (`git status` after staging) before committing.
- Commit message describes the closing work, not the session's narrative.

### 8. Report and stop

Report to the owner **without pushing**: starting and final SHA, the commit
chain and parents, files changed, evidence collected, what is still open, and
that nothing was pushed. Then mark the recovery marker `prepared`:

Write `.claude/session-recovery/close-marker.json` (gitignored):

```json
{"state": "prepared", "session_id": "<id>", "prepared_at": "<iso8601>",
 "head": "<sha>", "branch": "<branch>"}
```

Stop here. Do not push. Do not start new product work.

---

## Mode: finalize

### Gate — check this first, and stop if it fails

`finalize` runs **only** when the owner (or the GPT lead developer relayed by
the owner) has given an explicit push approval **in this chat**. A task prompt
saying "then push" written before the work existed is not approval of *this*
result.

If approval is absent, stop and say exactly that — do not push, do not ask
leading questions to manufacture consent, and do not treat `prepare`'s success
as approval.

### 1. Confirm nothing moved since prepare

- The prepared closing commit is still `HEAD` and its SHA is unchanged.
- The working tree still has nothing to add (beyond the permanently-excluded
  `.claude/settings.local.json`).
- If anything changed, re-run `prepare` instead of pushing a stale state.

### 2. Push — normal fast-forward only

```bash
git merge-base --is-ancestor origin/<branch> HEAD   # must succeed
git push origin <branch>:<branch>
```

- **No force-push**, ever.
- **Never push `main`** or any other branch.
- Confirm the push output shows a fast-forward (`old..new`, no `+` prefix).

### 3. Verify after push

- `local HEAD == origin/<branch>`
- `git rev-list --left-right --count origin/<branch>...HEAD` → `0 0`
- `main` and `origin/main` unchanged

### 4. CI — job level, not the workflow headline

Inspect per job (`gh run view <id> --json jobs`) and report **Python, Electron
and Mobile separately**. A workflow marked `success` can still contain a failed
`continue-on-error` job — `mobile` does exactly this here.

- Do not hide a first run behind a rerun. If a job was rerun, report both.
- Only rerun a failed job when its failure is **not explainable by the commit
  diff** (e.g. the known ChromaDB `no such table: acquire_write` flake), rerun it
  **once**, and do not claim the root cause is proven because the rerun passed.

### 5. Close out

Update the marker to `closed`:

```json
{"state": "closed", "session_id": "<id>", "prepared_at": "<iso8601>",
 "closed_at": "<iso8601>", "head": "<sha>", "branch": "<branch>"}
```

**Do not** write the closing commit's own SHA or its CI outcome back into
HANDOFF.md — that is the self-reference rule, and retro-editing the file after
push is exactly how it was broken before.

Then stop. **Do not start new product work.**
