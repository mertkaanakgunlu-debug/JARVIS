---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 05e28451813d3e0571d515e8d9bc376f6955132b
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
- This session started at **`b7abb5b`** (pushed; CI green, §4) and added **one
  work commit**, `05e28451813d3e0571d515e8d9bc376f6955132b`, parented directly on
  it. On top of that sits this closing documentation commit.
- Push state and CI for anything at or above `05e2845` are **derived live**, never
  stored here:

```bash
git rev-list --left-right --count origin/langgraph-migration...HEAD
gh run list --branch langgraph-migration    # then: gh run view <id> --json jobs
```

- Push requires the owner's explicit in-chat approval, every session, every time.

## 2. Last completed work

**Developer-productivity layer: targeted development verification plus a
delta-only task-spec contract** — 2026-08-07, one work commit `05e2845`. No file
under `jarvis/`, `electron/` or `mobile/` was touched, no dependency was added,
and no runtime behaviour changed.

- **`scripts/dev_verify.py`** turns a task's real diff (`<TASK_BASE_SHA>..HEAD`
  plus staged, unstaged and untracked changes, deduplicated and sorted) into a
  conservative deterministic verification plan. The default mode prints the plan
  with a reason per selected check and runs nothing; `--run` executes it, stops
  at the first failure, and reports everything after it as `NOT RUN` — never as
  passed.
- Two decisions a future reader would otherwise re-litigate: **every ambiguity
  resolves toward running more** (an unmappable module, a `conftest.py` or
  dependency edit, or an unclassifiable file yields `FULL PYTHON FALLBACK`, and
  the module-reference scan is a loose substring match on purpose), and **a git
  failure raises** instead of reading as "no changes" — an empty change set would
  otherwise produce an empty plan and a green-looking run that tested nothing.
- **A live workload can never be selected, in any mode.** The planner emits only
  `git`, `ruff`, `pytest`, `npm` and `flutter`; `tests/test_dev_verify.py` pins
  that structurally rather than trusting a comment.
- **Repository finding the mapping had to absorb:** `CLAUDE.md`, `.gitignore`,
  `.claude/settings.json`, `.claude/rules/*.md`, the session-close skill and
  `.github/workflows/ci.yml` look like documentation and are not —
  `tests/test_claude_session_hooks.py` reads each from the real repository root
  and asserts on its content. They route to that suite; a docs-only
  classification would have skipped a test that can genuinely fail.
- **Policy documents.** `CLAUDE.md`: task specs carry the delta only, and the
  canonical `ruff` + `pytest` pair is the work-completion / session-close
  standard rather than an iteration loop. `.claude/rules/testing.md`: the
  iteration / work-completion / session-close hierarchy is now canonical, and
  `FULL PYTHON FALLBACK` is an instruction rather than a warning.
  `.claude/skills/session-close/SKILL.md`: future HANDOFF rewrites must be state
  snapshots — the eight sections, the frontmatter contract, session identity,
  prepare/finalize, blocked state and push semantics are all unchanged.
- **No wall-clock figure was written into any of those documents, deliberately.**
  Two runs of the identical targeted selection on this machine differed by
  roughly 3× cold vs. warm (758.77s vs. 256.20s, same command, 2026-08-07), so a
  recorded number would mislead more than it helps.

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

Run 2026-08-07 on the tree of `05e2845` — the work commit, and the only later
change is this closing documentation commit:

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests
#   -> All checks passed!
.venv\Scripts\python.exe -m pytest -q
#   -> 3416 passed, 5 deselected, 362 warnings (642.80s)
git diff --check
#   -> clean
```

3416 is up from the previous snapshot's 3375 by **+41** — exactly the new
`tests/test_dev_verify.py`. Same tree, same date, targeted selection:

```powershell
.venv\Scripts\python.exe scripts\dev_verify.py --base b7abb5be650de0a7ab896ece055d7f93cb190542 --run
#   -> ruff OK; git diff --check clean;
#      pytest -q tests/test_claude_session_hooks.py tests/test_dev_verify.py
#      -> 220 passed (256.20s)
```

**Not run this session, and not claimed as passed:** Electron (`npm test`,
`npm run build`) and mobile (`flutter analyze`, `flutter test`) — neither
component changed. No live workload of any kind: no Ollama run, no A/B harness,
no completion-contract evaluation, no real mailbox.

**CI, read per job** (`gh run view 31191645327 --json jobs`, 2026-08-07): the
last pushed tip `b7abb5b` is green in **all three** jobs — `python`, `electron`
and `mobile` all `success`. That closes the previous snapshot's "no CI run exists
yet" gap for `e200658`/`b7abb5b`. Read this closing commit's own CI live once it
is pushed; it is not predicted here.

## 5. Known open issues

Each keeps its identifier; the detail stays in the linked document.

- **Completion-contract Findings 1–2 — open.** A completion repair can satisfy
  the contract from a *different* file than the one requested, and
  `honest_failure_retried` is blind to a repair that "succeeds" by substitution.
  Finding 3 (TTFB) was narrowed by `e200658`, not closed. See
  [`docs/eval/completion_contract_pilot_2026-08-05.md`](docs/eval/completion_contract_pilot_2026-08-05.md).
- **`completion_contract_ab.py`'s 4224s anomaly — instrumented, not explained.**
  A known eval-harness observability gap, explicitly not a production blocker; the
  proven-fact vs. hypothesis split is in
  [`docs/eval/completion_contract_ttfb_followup_2026-08-07.md`](docs/eval/completion_contract_ttfb_followup_2026-08-07.md).
- **`dev_verify.py` scope limits (new, deliberate).** Its Electron and mobile
  branches are unit-tested but have **never run live** — neither component
  changed this session. Cross-cutting widening covers `jarvis/graph/` and
  `jarvis/execution/` only; every other module relies on the direct filename
  match, the loose reference scan, or the full fallback.
- **`MOBILE-ASSETS-01` — a clean clone cannot build or test the mobile app.**
  `mobile/.gitignore` ignores `assets/fonts/*.ttf` and `flutter analyze` does not
  validate the pubspec `fonts:` section, so **a green `mobile` CI job is not
  evidence that the app builds anywhere.** See `mobile/assets/ASSETS_SETUP.md`.
- **`MOBILE-TEST-01` — `mobile/test/widget_test.dart` fails**, unchanged.
  `_SplashRouterState.initState` (`mobile/lib/app.dart:67`) starts an uncancelled
  `Future.delayed(Duration(seconds: 2))`. CI does not run `flutter test`.
- **Mobile L3 confirmation: still no approve/deny UI.** `classifyChatChunk()`
  shows a neutral "not yet supported" note instead of raw JSON, but nothing
  resolves the interrupt; the graph stays interrupted server-side.
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
  coarse type, not field CONTENT** — a deliberate scope limit.
- **Source Binding scope limits** (deliberate): bare-filename ambiguity across
  directories; plain Unicode casefold on Turkish İ/i; pre-execution guard has
  deterministic evidence only.
- **Faz 5 (mail → calendar) has never run against the real mailbox** — green on
  fixtures only; background ingestion stays off until it does.
- **Electron HUD confirmation is compile/parser-verified only — no live E2E**,
  and the Flutter app renders nothing for confirmations (above).
- **`python_run` is access-controlled, not sandboxed.**
- **Proactive turns gate L3 only**; an unwatched L2 write is mitigated by prompt
  instruction, not structurally closed.
- **Four `claude/*` scratch branches** hold commits unreachable from this branch
  (`eager-noether-46af01`, `gifted-wilbur-e021ea`, `stoic-spence-2c5246`,
  `thirsty-mclean-f67665`; counts last re-derived 2026-08-06). Do not delete
  without an explicit go-ahead.

## 6. Next engineering priority

**`MOBILE-TEST-01`.** One uncancelled timer (`mobile/lib/app.dart:67`) stands
between `mobile` having a lint gate and having a lint gate plus a smoke test.
Decide whether the fix belongs in the test or in `app.dart` — a splash timer that
outlives its widget is arguably the product bug.

**If the completion-contract line is picked back up**, Finding 1
(source-substitution repair) is higher-value than further TTFB polish: binding the
repair's success criterion to the *requested* source, not "a chart artifact exists
somewhere", is the pilot's own recommended next single step.

Do not start either — or any product work — inside a session that is closing.

## 7. Human-required actions

- **Google OAuth re-consent** (Gmail read, Calendar write, Contacts) — blocks the
  Faz 5 live measurement and the Faz 2 entity resolver.
- **Mobile font binaries** — owner deferred 2026-08-06; `MOBILE-ASSETS-01` stays
  open until decided.
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
- **Marker correction, 2026-08-07 — the previous snapshot was wrong here.** At
  this session's start `claude_session_state.py show` reported the on-disk marker
  as `state prepared`, session `f38d315b…`, head `b7abb5be…`: the session that
  produced `b7abb5b` **did** run `prepare`, then pushed, and never ran `close`.
  The previous §8 claimed the opposite — that the helper was deliberately never
  called and the newest marker was still `closed` at `9421b3c`. The disk is
  authoritative and that claim was false. Nothing was lost by it: the tree was
  clean and 0 ahead / 0 behind at this session's start, and `b7abb5b`'s CI is
  green in all three jobs (§4).
- This session ran `/session-close prepare` under its own identity, which
  overwrites the single marker file. The `f38d315b` prepared-but-never-closed
  record therefore survives only in this note.
- A next session whose preflight reports the previous session did not close, or
  whose SessionEnd identity was `UNVERIFIED`, reconciles before starting new work.
