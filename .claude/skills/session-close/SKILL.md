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

Open the file with the freshness metadata the SessionStart preflight reads:

```yaml
---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: <full 40-character SHA>
---
```

`covered_through_sha` is **`HEAD` right now — before you create the closing
commit.** It names the last *work* commit the document describes, never the
closing commit's own SHA (which does not exist yet, and naming it is the
self-reference bug). Get it with `git rev-parse HEAD` at this step, and use all
40 characters; a short SHA is rejected.

Done correctly, the next session's preflight reads `HANDOFF.md current` because
exactly one commit — the closing-doc commit — follows the covered work. Every
later commit that lands without a handoff refresh raises the count and the
preflight says `STALE` on its own.

Rules the file must obey (see `.claude/rules/documentation.md`):

- It must **not** contain its own closing commit's SHA, and must **not** predict
  its own push or CI outcome. Count the closing commit relationally.
- No `unpushed`, no `push approval pending` for this closing commit, no fixed
  ahead/behind number, no guess at this commit's CI result — those are derived
  live (`git rev-list --left-right --count`, `gh run view <id> --json jobs`),
  not stored.
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
that nothing was pushed. Then mark the recovery marker `prepared` — by running
the helper, never by writing the JSON yourself:

```powershell
.venv\Scripts\python.exe scripts\claude_session_state.py prepare
```

It reads the session identity from `.claude/session-recovery/current.json` (which
the SessionStart hook wrote from the real hook payload) and derives branch and
HEAD itself. It takes **no session-id argument** — see the identity rules below.

Stop here. Do not push. Do not start new product work.

---

## Session identity — never authored by hand

The marker used to be JSON the model typed, which meant its `session_id` was
whatever the model *believed* the session was called. A model cannot observe its
own session id; it can only infer one from a transcript filename, from "the
newest file in the directory", or from a guess — and a marker carrying a guessed
id certifies the wrong session. That is why every transition now goes through
`scripts/claude_session_state.py`:

```powershell
.venv\Scripts\python.exe scripts\claude_session_state.py prepare
.venv\Scripts\python.exe scripts\claude_session_state.py close
.venv\Scripts\python.exe scripts\claude_session_state.py block --run-id <id> --blocking-jobs python
.venv\Scripts\python.exe scripts\claude_session_state.py show
```

**Forbidden, without exception:**

- deriving a session id from a transcript filename or path;
- treating the newest transcript as "the current session";
- copying, retyping, or otherwise supplying a session id by hand;
- creating `current.json` yourself, or writing a marker when it does not exist;
- writing `close-marker.json` (or any recovery JSON) directly with an editor;
- working around a refusal from the helper by hand-writing the JSON it declined.

If the helper refuses — no `current.json`, an identity mismatch, a moved HEAD —
**report the refusal verbatim to the owner and stop.** A refusal is a real
finding about the session's state, not an obstacle to route around. A session
whose identity cannot be established simply does not get a marker; say so in the
report rather than manufacturing one.

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
and Mobile separately**. **The overall workflow headline is not a decision
source**: a run marked `success` can still contain a failed `continue-on-error`
job — `mobile` does exactly this here.

Classify **every** failed job explicitly before going near the marker:

| job | classification |
|---|---|
| `python` | **blocking** |
| `electron` | **blocking** |
| `mobile` | **blocking.** `CI-MOBILE-01` was cleared at the source on 2026-08-06, so the old "known cosmetic signature" exemption is **gone** — there is no `mobile` failure that may be waved through any more. It is still `continue-on-error: true`, so the workflow headline stays green while the job is red: read the job. If the failure is new `deprecated_member_use` findings that the diff cannot explain, suspect the unpinned `channel: stable` Flutter version (`.claude/rules/mobile.md`) — that is a diagnosis, not an exemption. |

Rerun rules:

- A failure **explained by the commit diff is deterministic — never rerun it.**
  Fix it instead. (A rerun cannot make a wrong assertion right, and re-running
  it reads as hoping rather than diagnosing.)
- The known ChromaDB `no such table: acquire_write` flake may be rerun **once**.
- If the rerun also fails, the job stays **blocking**.
- Never hide the first run behind a rerun — report both.
- A passing rerun does **not** prove a root cause.

### 5. Close out — `closed` has to be earned

Run `close` **only** when all three hold:

1. push and every post-push remote verification succeeded;
2. **no blocking CI failure remains** (per the table above);
3. any permitted rerun has completed **and passed**.

```powershell
.venv\Scripts\python.exe scripts\claude_session_state.py close
```

The helper enforces the *identity* half of this (same session, same HEAD, same
branch, previous state `prepared`) and refuses otherwise. The **CI
classification above is yours** — the helper cannot tell a blocking failure from
a known non-blocking one, and it will not stop you writing `closed` over a red
tip. Judge the table first, then run the command.

If a blocking failure remains, the session is **not closed**:

```powershell
.venv\Scripts\python.exe scripts\claude_session_state.py block --reason-code CI_BLOCKING_FAILURE --run-id <run> --blocking-jobs python
```

…and **do not close the session**. Report the failure, its classification, and
the fix — do not soften a red tip into a clean close. This rule exists because
it was broken: a session once wrote `closed` while the branch tip's `python`
job was failing, which is the same "report an unfinished check as passed" error
the whole reporting standard forbids.

**Do not** write the closing commit's own SHA or its CI outcome back into
HANDOFF.md — that is the self-reference rule, and retro-editing the file after
push is exactly how it was broken before. The marker is the right home for a CI
outcome: it is local, gitignored, and written *after* the run finished.

Then stop. **Do not start new product work.**
