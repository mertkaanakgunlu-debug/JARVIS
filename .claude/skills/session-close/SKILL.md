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

**If the preflight reports the PREVIOUS session as `FINALIZE BLOCKED by CI`,**
that is inherited state, not this session's. Read its `reason_code` first
(`claude_session_state.py show`):

- `CI_BLOCKING_FAILURE` — a real red job on the tip. Fix it before closing
  anything on top of it.
- `CI_INFRA_UNAVAILABLE` — the tip was never judged. That is the *absence* of a
  result, so treat it as neither a verdict about the code nor evidence the code
  is fine. An inherited infrastructure block does not by itself stop this
  session working: the old marker stays exactly as it is, and this session's own
  commit and push produce the next real CI evidence.

**An inherited blocked marker is never relabelled, and never reopened.**
`CI_INFRA_UNAVAILABLE` was that session's honest terminal outcome and stays on
the record — do not go back and turn it into `closed`. This session prepares
under its **own** identity (the helper refuses if the identity has not actually
changed) and earns its own verdict.

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

#### Keep it a snapshot, not an account of the session

HANDOFF is imported by `CLAUDE.md` and therefore read in full at the start of
every session, so every line it carries is paid for on every future turn. Its
job is to leave the next session **oriented**, not **informed about this one**.

- **No investigation chronology.** What was tried, in what order, and which
  branches turned out to be dead ends is the session's story; `git log` and
  `CHANGELOG.md` own it.
- **No re-narrated root cause or diff.** Name the behaviour that changed and the
  one or two technical decisions a future reader would otherwise re-litigate.
  The mechanism is in the code and the commit that introduced it.
- **Never re-copy evidence that already has a canonical home.** A/B tables,
  per-trial numbers, harness internals and gate arithmetic live in
  `docs/eval/**`, `CHANGELOG.md` and the relevant technical document — link to
  the file, quote only the single number the decision actually turned on.
- **Summarise §2 at behaviour + decision + verification level**: what now
  behaves differently, why it was built that way, and what was genuinely run to
  check it.

**Compaction removes retelling, never state.** Anything the next session needs in
order to recover stays in full, however long it is: a lifecycle step that was
skipped or deliberately not run, a refusal from `claude_session_state.py`, an
inherited blocked marker and its reason code, an open issue with its identifier,
a human-required action, a decision the owner made that the code does not show.
If you are unsure whether a line is retelling or state, ask whether a session
starting cold would *act differently* without it — if yes, it is state.

The honesty rules above are unchanged and are not traded against brevity: every
test and CI claim still carries its command and date, an unrun check is still
written as unrun, and no number is quoted from a document instead of a run. The
eight sections and the frontmatter contract are unchanged too — this governs
what goes *inside* them.

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

`prepare` **refuses rather than overwrites** in two cases. Both are findings to
report, not obstacles to route around:

- **This session already holds a `blocked` marker.** Blocking ends the session
  (*finalize §4*); re-preparing would walk the block back to `prepared`.
- **A marker file exists but is not trustworthy as a marker.** *Missing* and
  *unreadable* are different states. Missing means there is nothing to lose —
  that is the ordinary path and it still works. Unreadable covers two things,
  and both stop `prepare`: the file will not parse at all, **or** it parses fine
  but is not the shape a genuine marker has — `{}`, or a `blocked` entry missing
  its own `session_id`. Neither is proof of nothing: it could be a `blocked`
  marker truncated mid-write, with its own evidence still legible in it, or with
  just enough missing that reading it as "not blocked" or "someone else's block"
  would be a guess. `prepare`'s very next act is to overwrite that file, so it
  stops instead.

  **Do not delete, repair, rename, regenerate or hand-write the marker**, and do
  not guess which session it belonged to. Report the refusal verbatim, leave the
  bytes exactly as they are, and stop — it is recovery evidence, and what happens
  to it is the owner's call. There is deliberately no repair command.

  Note the asymmetry with the SessionStart hook, which stays fail-**open** on the
  same file: a session must always be able to *start*. Certifying a close is a
  *claim*, and a claim made over unreadable evidence is exactly what this refuses
  to make.

  `close` and `block` recognise the same distinction and say so accurately: no
  marker at all still reads `no close marker exists -- run \`prepare\` first`,
  but a marker that exists and cannot be trusted says so explicitly rather than
  claiming there is nothing there.

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

#### Classify the FAILURE, not the conclusion

`failure` and `cancelled` both appear in all three classes below, so **a job's
conclusion never classifies it on its own.** Open the log before you decide:

```bash
gh run view --job <job-id> --log
```

| class | what the log shows | what to do |
|---|---|---|
| **Diff-explained** | the job reached its real step (`Test (pytest)`, `Lint (ruff)`, `flutter analyze`, `npm test`, `build`) and *that step* failed on repository content | **never rerun it.** Fix it. A rerun cannot make a wrong assertion right, and re-running it reads as hoping rather than diagnosing. |
| **`CI-FLAKE-CHROMA-01`** | `chromadb … no such table: acquire_write`, in files the commit never touched | one rerun, per the budget below |
| **`CI_INFRA_UNAVAILABLE`** | the provider never reached a verdict — see the signatures below | one rerun, then the session ends blocked — see the terminal rule below |

A failure is `CI_INFRA_UNAVAILABLE` only if the log actually shows the provider
failing, not the repository. Signatures, all observed on run `31117623901`
(2026-08-06):

- `Failed to resolve action download info. Error: Service Unavailable` during
  `Set up job` — the runner could not even fetch `actions/checkout`, so no repo
  content was ever read;
- the runner dying or being reclaimed before checkout completes;
- a job or workflow **cancelled from outside** so its tests could not finish —
  e.g. `Test (pytest)` logging `KeyboardInterrupt` then
  `##[error]The operation was canceled.` after 2704 passing tests.

**Never assign `CI_INFRA_UNAVAILABLE` from a conclusion alone.** `cancelled`
also happens when a human cancels a run, and `failure` is what a genuinely
broken build looks like. Quote the log line you classified from, in the report
and in the marker's `run_id`. If the log does not show a provider failure, the
job is `CI_BLOCKING_FAILURE` — the honest default when you cannot prove
otherwise.

An infrastructure failure means **the tree has no verdict**, not that it passed.
Never let one stand in for a green run.

#### Rerun budget — bounded, and spendable once

- The **first run is always reported**, whatever the rerun does. Never hide it.
- At most **one** rerun of a given run, and only for a failure that is *not*
  diff-explained.
- **Never rerun the same run repeatedly.** Re-running until it goes green is the
  same error as reporting an unrun check as passed.
- If the rerun still produces no full verdict, the jobs stay **blocking** and
  the session is blocked. That budget is spent, and there is no third attempt —
  see the terminal rule below.
- A passing rerun does **not** prove a root cause.

#### `CI_INFRA_UNAVAILABLE` is TERMINAL for the session

Once the one rerun is spent and the provider still returned no verdict, the
session ends blocked. There is no honest way to conjure a verdict for a tip that
was never judged, and every route that looks like one is worse than the block:

- **Do not manufacture an empty commit** to re-fire `push`. That fabricates
  history to buy a green check.
- **Do not keep re-running the exhausted run.** The budget above is one.
  Re-running until it goes green is the same error as reporting an unrun check
  as passed.
- **Do not add a `workflow_dispatch` trigger** to get a manual re-run. GitHub
  resolves `workflow_dispatch` from the repository's **default branch**, which
  here is `main` — and `main` carries no `.github/` directory at all, so a
  trigger added only on `langgraph-migration` is not a mechanism anyone can rely
  on. Making it real would mean writing to `main` or changing the default
  branch: both need the owner, and neither belongs inside a close.
- **Do not touch `main`**, move the workflow, or change the default branch to
  work around this.

So report it and stop:

1. Report the first run **and** its one rerun, per job, quoting the log line you
   classified from. Neither is hidden behind the other.
2. `block --reason-code CI_INFRA_UNAVAILABLE --run-id <run> --blocking-jobs …`.
3. Do **not** `close`, and do **not** re-`prepare`. `prepare` is refused for a
   session that already holds its own `blocked` marker
   (`scripts/claude_session_state.py`) — `blocked → prepared → closed` is
   `blocked → closed` with one extra step, and the helper now enforces that
   rather than trusting this paragraph.

**That marker is not a debt the next session has to pay off.** A later session
sees it, reads `CI_INFRA_UNAVAILABLE`, knows no code failure was ever
demonstrated, and works normally under its own identity — the inherited marker
does not block it. Its commits push, and *that* push produces the next run to
read per job. Nothing has to retroactively become `closed` for the protocol to
be whole: an honest terminal state is a finished session, not a stuck one.

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

If a blocking failure remains, the session is **not closed**. The reason code is
a **fixed vocabulary** of exactly two values — the helper refuses anything else —
and which one you pick is the classification from §4, recorded:

```powershell
# the repository is at fault: a failing test, a lint error, a broken build
.venv\Scripts\python.exe scripts\claude_session_state.py block --reason-code CI_BLOCKING_FAILURE --run-id <run> --blocking-jobs python

# the provider never reached a verdict -- only after reading the job log
.venv\Scripts\python.exe scripts\claude_session_state.py block --reason-code CI_INFRA_UNAVAILABLE --run-id <run> --blocking-jobs python,electron
```

…and **do not close the session**. Report the failure, its classification, and
the fix — do not soften a red tip into a clean close. This rule exists because
it was broken: a session once wrote `closed` while the branch tip's `python`
job was failing, which is the same "report an unfinished check as passed" error
the whole reporting standard forbids.

`CI_INFRA_UNAVAILABLE` is **not a softer `CI_BLOCKING_FAILURE`.** It says
something narrower and checkable: the jobs did not run, so the tree is unproven
in both directions. Do not reach for it because a red job is inconvenient — if
the log does not show the provider failing, the code is what failed.

**A blocked marker is never relabelled, and never re-prepared.** The helper
refuses `blocked → closed` *and* `blocked → prepare` for the session that owns
the marker, so there is no one-step and no two-step route out. Blocking ends the
session; the honest next step belongs to the NEXT session, under a different
identity. If the helper refuses, report the refusal verbatim and stop — do not
hand-write the JSON it declined.

**Do not** write the closing commit's own SHA or its CI outcome back into
HANDOFF.md — that is the self-reference rule, and retro-editing the file after
push is exactly how it was broken before. The marker is the right home for a CI
outcome: it is local, gitignored, and written *after* the run finished.

Then stop. **Do not start new product work.**
