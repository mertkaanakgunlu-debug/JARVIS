---
paths:
  - "tests/**"
  - "scripts/**"
---

# Tests and measurement

Run from the repo root with the venv interpreter:

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m ruff check jarvis scripts tests
```

`pytest-timeout` is **not installed** — `--timeout=` is a usage error, not an
option. Re-derive the suite's size from the run you actually did; never quote a
count from a document.

## Development verification hierarchy

Three levels, and the level is chosen by *where you are in the work*, not by how
confident you feel. The full suite costs minutes, not seconds — time it on the
run you actually did rather than quoting a figure from here. Running it after
every edit is the largest avoidable cost in a development loop; running it
*less* than once before a commit is the largest avoidable risk.

| level | when | what |
|---|---|---|
| **Iteration** | after each edit | targeted deterministic checks — `scripts/dev_verify.py --base <TASK_BASE_SHA> --run` |
| **Work completion** | once, when the change is stable | full verification of each **touched** component |
| **Session close / CI** | at `/session-close` and on push | the canonical full verification, unchanged |

`TASK_BASE_SHA` is `git rev-parse HEAD` at the start of the task — recorded then,
not re-derived later, so the selector sees the task's real diff (committed work
since the base, plus staged, unstaged and untracked changes) rather than only
what is uncommitted right now.

Without `--run` the selector prints its plan and executes nothing. Read the plan:
it names every selected check and why, so a wrong mapping is visible rather than
silent.

**A targeted plan is not automatically a cheap one.** `CLAUDE.md`, `.gitignore`,
`.claude/settings.json`, `.claude/rules/*.md`,
`.claude/skills/session-close/SKILL.md` and `.github/workflows/ci.yml` look like
documentation, but their content is asserted by
`tests/test_claude_session_hooks.py`, which drives the real hook scripts as
subprocesses and is among the slowest files in the suite. Editing one of them
selects it — correctly — and the saving over a full run is modest rather than
dramatic. No figure is written down here on purpose: two runs of the *same*
selection on this machine differed by roughly 3x between a cold and a warm run,
so time your own run instead of trusting a remembered number. The large saving
is on ordinary module changes.

**`FULL PYTHON FALLBACK` is an instruction, not a warning.** The selector emits
it when a change's impact is not derivable — an unmapped module, a `conftest.py`
edit, a dependency bump, an unclassifiable file. Run the full suite then; do not
hand-pick a subset the tool declined to pick.

Work completion means the components the change actually touched:

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests   # Python changed
.venv\Scripts\python.exe -m pytest -q
git diff --check
```

…plus `npm test` (in `electron/`) only if Electron changed, and `flutter
analyze` / `flutter test` (in `mobile/`) only if mobile changed. **An untouched
component's suite is not run out of habit** — a green run over code the diff
never reached is not evidence about the diff, and it is often mistaken for some.

Nothing here relaxes the rules below it. `dev_verify.py` never selects a live
workload — no Ollama run, no A/B harness, no completion-contract evaluation, no
real mailbox, no manual acceptance driver — in any mode. Those stay explicit,
pre-registered, and run alone. A pre-registered eval gate is still decided by
its own full protocol, never by a targeted development run.

## Isolation

**Before writing a test that constructs `SessionStore`, `UsageTracker`,
`kill_switch`, or anything else resolving paths as `Path("data")/…` relative to
cwd, use the `isolated_cwd` fixture** in `tests/conftest.py` (there is also
`jarvis_home`). `chdir` alone is insufficient — see the fixture's own docstring.
Tests must never write into the owner's real `data/`, real `.claude` home, or
real sessions.

Extend `tests/` rather than reintroducing ad-hoc throwaway scripts for anything
touching shared logic (safety kernel, stores, routing).

## Measurement discipline — every line here was bought with a wrong result

- **Never report a single-sample measurement.** Live-model scores need n ≥ 10.
  Say plainly when data is synthetic vs. the owner's real mailbox.
- **Measure the lever, not the workload.** In an A/B hold the request fixed and
  change exactly one variable, and always put an accuracy axis next to a latency
  axis.
- **Measure with the real input.** Reconstructing a deterministic function's
  input can change the result and make the whole analysis unfair.
- **Check the denominator for vacuous passes.** A "did not change" assertion
  passes trivially on empty state. Compute eligibility from the state *before*
  the round and report three-valued (pass / fail / not-applicable).
- **Re-run the gate after fixing it.** A prediction that a fix improves a gate
  is not evidence; the run is. Predictions here have been flatly wrong.
- **Honour the pre-registered threshold.** Do not invent a reason to skip
  confirmation after seeing a pilot's headline; a pilot headline has already
  failed to reproduce.
- **Read CI at job level.** A green workflow hides failing `continue-on-error`
  jobs (`mobile` here). Use `gh run view <id> --json jobs`.
- **Live testing catches what units miss.** A gate once passed 2235 tests and
  38/38 mutations, then failed 10/10 live because it scored the model's
  arguments rather than the user's request.
- **A live harness runs alone** — no other model work, no second harness,
  nothing else on the GPU. A previous measurement was ruined exactly that way.

Raw eval output goes to `.eval-results/` (gitignored, never overwritten);
committed summaries live in `docs/eval-results/` and carry the raw file's
sha256 so a decision's numbers stay checkable.
