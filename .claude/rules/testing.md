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
