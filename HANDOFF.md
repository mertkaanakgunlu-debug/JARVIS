---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 3e6304c8e695ef1495a515482d78c5303f4d2b85
---

# HANDOFF — current state

Single-state snapshot, rewritten at each session close. Not a history: `git log`
and `CHANGELOG.md` own that. **If anything here contradicts the repository, the
repository is right and this file is the bug** — re-derive rather than trust.

Imported by `CLAUDE.md`, so it is read in full every session. Keep it a
snapshot: no investigation chronology, no re-narrated root cause or diff, no
evidence copied out of `docs/eval/**` or `CHANGELOG.md` — link instead. The
compaction rules are in `.claude/skills/session-close/SKILL.md`.
`covered_through_sha` names the last **work** commit described here, never this
file's own closing commit.

## 1. Current verified state

- Branch **`langgraph-migration`**. `main` is `5f6f6ff` and a strict ancestor;
  never quote how far behind it is — derive it:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`
- This session started at `5576a1d` and is unusual: it closed **once**, mid-way.
  The chain, which does not change, is
  `3e6304c ← f3b6a55 ← b8cb4bd ← 7148062 ← 5576a1d`, and this closing
  documentation commit sits on top of `3e6304c`.
  - `7148062` — mobile splash-timer fix; its closing doc commit **`b8cb4bd` was
    pushed and is CI-green in all three jobs** (§4).
  - `f3b6a55`, `3e6304c` — the developer-productivity pair described in §2,
    added after that close under the owner's explicit instruction.
- Push state and CI for anything above `b8cb4bd` are **derived live**, never
  stored here:

```bash
git rev-list --left-right --count origin/langgraph-migration...HEAD
gh run list --branch langgraph-migration    # then: gh run view <id> --json jobs
```

- Push requires the owner's explicit in-chat approval, every session, every time.

## 2. Last completed work

Two work commits, 2026-08-07/08. No file under `jarvis/`, `electron/` or
`mobile/` was touched by either; no runtime behaviour changed.

**`f3b6a55` — CI's pytest step runs in parallel.** `pytest -n 4 --dist load`,
with the worker count **pinned** rather than `-n auto`: 4 is what was measured,
and `auto` silently changes meaning if the runner is resized. The suite could
not run in parallel at all before this — `tests/test_execution_approval.py` built
a parametrize id from the wall clock, so collection differed between processes
and xdist refused to start; a fixed constant asserts the same thing. Two
decisions a future reader would otherwise re-litigate are in the job's own
comment: why `load` beat `loadfile`, and the one 30s subprocess timeout observed
at one worker per core — **the same ratio a 4-vCPU runner has, so CI-side
contention is genuinely unmeasured until this lands** (§5). Rollback is `-n 2
--dist load` or a bare `pytest`; coverage is identical in every mode.

**`3e6304c` — `/session-close` reuses a full run instead of repeating it.** A
closing commit that only touches `HANDOFF.md`/`CHANGELOG.md` used to re-run the
whole suite: ten minutes that tested nothing new, while the one artefact it
*does* introduce had no test. Now the earlier run is reused — but only against
machine-authored evidence in the gitignored
`.claude/session-recovery/full-verification.json`, written through the same
single write API as the marker. Head, branch, session and timestamp are derived;
only the exit status and pytest's own summary line come from outside, and prose
is refused at write time. **There is deliberately no `record` subcommand** — a
subcommand taking a count would be a prompt where evidence could be typed.
`scripts/dev_verify.py --full` is the only producer and refuses a dirty tree,
because evidence is keyed by commit. Every way of not having usable evidence
(none, failed, foreign session, rewritten history, any non-document change since
the verified tree, or never asking) yields `FULL PYTHON FALLBACK`. New
`tests/test_handoff_contract.py` checks the closing artefact itself against the
preflight's **imported** parser.

Also this session, and already documented where it belongs: `MOBILE-TEST-01` was
closed in `7148062` (`.claude/rules/mobile.md`, `CHANGELOG.md`).

## 3. Operational modes and rollout decisions

Defaults re-read from `jarvis/config.py` on 2026-08-07 (verify there, not here).
**This session changed no default and made no rollout decision.**

| setting | default | note |
|---|---|---|
| `required_outputs_mode` | `off` | Gate pre-registered and still unpassed; `object_created` delta and the corpus-B false-positive clauses remain the blockers. |
| `execution_contract_mode` | `shadow` | Honesty kernel. `enforce` gated on 100 real artifact operations with 0 reported false blocks. |
| `confirmation_gate_enabled` | `True` | The L3 gate is live in every interface. |
| `external_writes_enabled` | `True` | `--profile test` flips it off. |
| `monitor_proactive_enabled` | `False` | Proactive turns off by default. |
| `calendar_from_mail_enabled` | `False` | Faz 5; never run against a real mailbox. |
| `cloud_policy` / `local_model` | `off` / `qwen3:8b` | Local-only by default. |

Do not change a pre-registered threshold, corpus or metric after seeing a result.
**Mobile font binaries stay out of the repository** (owner decision 2026-08-06,
untouched); the consequence is `MOBILE-ASSETS-01` in §5.

## 4. Tests and CI

Full verification of the tree of `3e6304c` — the second work commit, and the
only later change is this closing documentation commit — run through the
recorder, which is why §6's targeted branch was available at all:

```powershell
.venv\Scripts\python.exe scripts\dev_verify.py --full
#   -> git diff --check clean; ruff All checks passed!
#      pytest -q -> 3448 passed, 5 deselected, 362 warnings (622.80s)
#      recorded at 3e6304c8
```

3448 is up from the previous snapshot's 3416 by **+32**: `test_handoff_contract`
6, `test_dev_verify` 13, `test_claude_session_hooks` 13.

Parallel-mode measurements, 2026-08-08, all on one tree whose fingerprint was
taken before and after the batch and matched, 3446 tests and exit 0 in each row:
serial `796.96s`; `-n 4 --dist loadfile` `367.08s`; `-n 4 --dist load`
`234.31s`. The chosen mode was then re-run twice more, green both times
(`240.80s`, `241.13s`). One `-n auto --dist load` run at 32 workers on 32 cores
failed with a single 30s subprocess timeout — the observation §5's open item is
about.

**Not run this session, and not claimed as passed:** Electron (`npm test`,
`npm run build`) — untouched. Mobile `flutter test` / `flutter analyze` were run
for `7148062` (see `.claude/rules/mobile.md`) and not re-run for these two
commits, which change no mobile file. No live workload of any kind: no Ollama
run, no A/B harness, no completion-contract evaluation, no real mailbox.

**CI, read per job** (`gh run view 31213026002 --json jobs`, 2026-08-07):
`b8cb4bd` is green in **all three** jobs — `python`, `electron`, `mobile` all
`success`; the `python` job took 13m34s, of which the pytest step was 630s. That
run used the OLD serial command. **No CI run has ever executed `pytest -n 4
--dist load`** — read this session's own commits' CI live once pushed; it is not
predicted here.

## 5. Known open issues

Each keeps its identifier; the detail stays in the linked document.

- **CI-side contention for the parallel pytest step is unmeasured (new).** The
  only observed subprocess timeout came at one worker per core, which is the
  ratio `-n 4` has on a 4-vCPU `windows-latest` runner; the local runs that were
  green had four times the headroom. If `python` goes red on a subprocess
  timeout with no diff to explain it, that is this — drop to `-n 2 --dist load`
  or revert to a bare `pytest` (`.github/workflows/ci.yml` carries the numbers).
- **Completion-contract Findings 1–2 — open.** A completion repair can satisfy
  the contract from a *different* file than the one requested, and
  `honest_failure_retried` is blind to a repair that "succeeds" by substitution.
  Finding 3 (TTFB) was narrowed by `e200658`, not closed. See
  [`docs/eval/completion_contract_pilot_2026-08-05.md`](docs/eval/completion_contract_pilot_2026-08-05.md).
- **`completion_contract_ab.py`'s 4224s anomaly — instrumented, not explained.**
  A known eval-harness observability gap, explicitly not a production blocker; the
  proven-fact vs. hypothesis split is in
  [`docs/eval/completion_contract_ttfb_followup_2026-08-07.md`](docs/eval/completion_contract_ttfb_followup_2026-08-07.md).
- **`MOBILE-ASSETS-01` — a clean clone cannot build or test the mobile app.**
  `mobile/.gitignore` ignores `assets/fonts/*.ttf` and `flutter analyze` does not
  validate the pubspec `fonts:` section, so **a green `mobile` CI job is not
  evidence that the app builds anywhere.** It is also the only thing between
  `flutter test` and CI (§7). See `mobile/assets/ASSETS_SETUP.md`.
- **`dev_verify.py` scope limits.** Its **Electron branch has still never run
  live**. Cross-cutting widening covers `jarvis/graph/` and `jarvis/execution/`
  only. It deliberately does **not** select `flutter test` during iteration (§7).
- **Mobile L3 confirmation: still no approve/deny UI.** `classifyChatChunk()`
  shows a neutral "not yet supported" note instead of raw JSON, but nothing
  resolves the interrupt; the graph stays interrupted server-side. This is the
  largest functional gap on mobile.
- **Mobile `flutter analyze` runs with the DEFAULT analyzer rule set** — no
  `analysis_options.yaml` anywhere, so `flutter_lints` is never applied.
- **CI's Flutter version is unpinned** (`subosito/flutter-action@v2`,
  `channel: stable`) — a new stable release can redden `mobile` with no code
  change.
- **`CI-FLAKE-CHROMA-01` — transient suspected, root cause unproven.**
  `chromadb>=0.6` is unpinned. Rerun a failed job **once** only when
  unexplainable by the diff; a passing rerun never proves a root cause.
- **A lost CI verdict (`CI_INFRA_UNAVAILABLE`) has no recovery mechanism** beyond
  "the next push judges the next tip" — a documented, deliberate limit
  (`workflow_dispatch` cannot work on this repo's default-branch layout).
- **`claude_session_state.py`'s marker structural check validates presence and
  coarse type, not field CONTENT** — a deliberate scope limit. The new
  verification record is checked the same way, plus a pattern on the summary.
- **Source Binding scope limits** (deliberate): bare-filename ambiguity across
  directories; plain Unicode casefold on Turkish İ/i; pre-execution guard has
  deterministic evidence only.
- **Faz 5 (mail → calendar) has never run against the real mailbox** — green on
  fixtures only; background ingestion stays off until it does.
- **Electron HUD confirmation is compile/parser-verified only — no live E2E.**
- **`python_run` is access-controlled, not sandboxed.**
- **Proactive turns gate L3 only**; an unwatched L2 write is mitigated by prompt
  instruction, not structurally closed.
- **Four `claude/*` scratch branches** hold commits unreachable from this branch
  (`eager-noether-46af01`, `gifted-wilbur-e021ea`, `stoic-spence-2c5246`,
  `thirsty-mclean-f67665`; counts last re-derived 2026-08-06). Do not delete
  without an explicit go-ahead.

## 6. Next engineering priority

**Read the first CI run that executes `pytest -n 4 --dist load`, per job.** It is
the only unmeasured half of `f3b6a55`, and the rollback is one line.

After that, **completion-contract Finding 1 (source-substitution repair)**:
binding the repair's success criterion to the *requested* source rather than "a
chart artifact exists somewhere" is the pilot's own recommended next single step.
On mobile the next real product gap is the **L3 approve/deny UI** (§5).

Do not start any of this — or any product work — inside a session that is
closing.

## 7. Human-required actions

- **Google OAuth re-consent** (Gmail read, Calendar write, Contacts) — blocks the
  Faz 5 live measurement and the Faz 2 entity resolver.
- **Mobile font binaries** — owner deferred 2026-08-06. With `MOBILE-TEST-01`
  closed, this is now the *only* blocker to running `flutter test` in CI.
- **Should `flutter test` join `dev_verify.py`'s iteration loop?** Left unchanged
  deliberately: the tool would then assume font assets on every checkout.
- **Default-branch / `.github/` layout** — `main` carries no `.github/`
  directory. Flagged for the owner to decide, not decided on their behalf.

Push approval is per-session and per-action: requested in chat at the time of the
push, never recorded here.

## 8. Session recovery notes

- A **SessionStart** hook (`scripts/claude_session_start.py`) injects the
  repository preflight; it is fail-open (`SESSION PREFLIGHT DEGRADED` → re-derive
  manually). Its only write is the gitignored `current.json` identity record. A
  **SessionEnd** hook writes `.claude/session-recovery/latest.json` on every exit.
  Neither commits, pushes, or edits a tracked file.
- Session identity is machine-authored. Every lifecycle transition goes through
  `scripts/claude_session_state.py` (`prepare`/`close`/`block`/`show`), which
  takes no id argument. If it refuses, report the refusal verbatim and stop.
- **This session ran the lifecycle twice, deliberately.** It prepared, pushed and
  `closed` at `b8cb4bd` after CI came back green in all three jobs; the owner
  then authorised further work, so `f3b6a55` and `3e6304c` were built and the
  session prepared again over its own `closed` marker. That transition is
  allowed (only `blocked` is terminal), and the earlier `closed` record survives
  only in this note.
- **A third gitignored file now exists**: `full-verification.json`, the
  reusable-full-run evidence. It is written only by `dev_verify.py --full`, is
  bound to a session id and a commit, and is not transferable — a next session
  reading it will correctly find it unusable and run the suite.
- This session's preflight warned that the previous session had not closed. It
  was a false alarm: the marker on disk read `closed` at `5576a1d`, and the
  breadcrumb belonged to a later session that exited on a clean tree with nothing
  to lose. Check the marker itself before believing that warning.
