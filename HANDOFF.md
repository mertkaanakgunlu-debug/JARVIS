---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 71480629a8b098e538d1755cc60e214175c169e6
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
- This session started at **`5576a1d`** (pushed; CI green in all three jobs, §4)
  and added **one work commit**, `71480629a8b098e538d1755cc60e214175c169e6`,
  parented directly on it. On top of that sits this closing documentation
  commit.
- Push state and CI for anything at or above `7148062` are **derived live**,
  never stored here:

```bash
git rev-list --left-right --count origin/langgraph-migration...HEAD
gh run list --branch langgraph-migration    # then: gh run view <id> --json jobs
```

- Push requires the owner's explicit in-chat approval, every session, every time.

## 2. Last completed work

**`MOBILE-TEST-01` closed — the splash router's timer no longer outlives the
widget tree** — 2026-08-07, one work commit `7148062`. Product-code fix, not a
test workaround. Four files: `mobile/lib/app.dart`,
`mobile/test/widget_test.dart`, `scripts/dev_verify.py`,
`.claude/rules/mobile.md`. No dependency added, no `pubspec.yaml` change.

- `_SplashRouterState` armed an uncancellable `Future.delayed(2s)`; it now holds
  a cancellable `Timer` and cancels it in `dispose()`. **Splash behaviour is
  unchanged** — same 2s delay, same `pushReplacementNamed('/home')`, same
  `mounted` guard. It was the only leak in `mobile/lib/`: `lock_screen.dart` and
  `home_screen.dart`'s `_TopBarState` already cancelled theirs.
- **The two decisions a future reader would otherwise re-litigate are recorded
  where mobile work loads them automatically** — `.claude/rules/mobile.md`'s
  Tests section. In short: the guard test disposes the tree *inside* the 2s
  window and must never elapse fake time past the deadline (elapsing lets a
  leaked timer retire itself, so the invariant passes **vacuously**), and
  `pumpAndSettle()` can never be used in this tree because `JarvisOrb`'s
  controller `repeat()`s forever.
- **Both guards were falsified before being trusted**: reverting the fix and
  re-running turns the smoke test *and* the disposal guard red while the
  navigation test stays green — so the guard carries the signal and the
  behaviour test pins what the fix must not change.
- **Repository finding the fix exposed.** `scripts/dev_verify.py` justified
  keeping the mobile iteration loop to the analyzer with "`flutter test` still
  carries the known MOBILE-TEST-01 failure" — false as of this commit. The
  reason now names the real constraint (`flutter test` needs the gitignored font
  assets, `MOBILE-ASSETS-01`). **The planner's behaviour is deliberately
  unchanged**; whether `flutter test` joins the iteration loop is a cost
  decision for the owner, not a side effect of this fix (§7).
- **`MOBILE-ASSETS-01` was not touched**: no placeholder fonts, no pubspec
  change, and `.github/workflows/ci.yml` still runs `analyze` only.

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

Run 2026-08-07 on the tree of `7148062` — the work commit, and the only later
change is this closing documentation commit:

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests
#   -> All checks passed!
.venv\Scripts\python.exe -m pytest -q
#   -> 3416 passed, 5 deselected, 362 warnings (578.57s)
git diff --check
#   -> clean
```

3416 is unchanged from the previous snapshot: this session added Dart tests, not
Python ones. Targeted selection on the same tree — **the first live run of
`dev_verify.py`'s mobile branch**, which the previous snapshot listed as never
executed:

```powershell
.venv\Scripts\python.exe scripts\dev_verify.py --base 5576a1d8051517ae162045e1e531f8d51ad2bbe5 --run
#   -> git diff --check; ruff; pytest -q tests/test_claude_session_hooks.py
#      tests/test_dev_verify.py; (mobile) flutter analyze  -- all 4 PASSED
```

Mobile, run 2026-08-07 in `mobile/` with **Flutter 3.44.6 / Dart 3.12.2** and the
gitignored font binaries present locally:

```powershell
C:\flutter\bin\flutter.bat test      # -> 17 passed (widget 3, chat_sse 10, transcript 4)
C:\flutter\bin\flutter.bat analyze   # -> No issues found!
```

Before the fix, the same `flutter test` reproduced `MOBILE-TEST-01` live
(`'!timersPending'`, `flutter_test/src/binding.dart:2542`). **That green result
depended on local font binaries and is not evidence that a clean clone or CI can
run `flutter test`** (`MOBILE-ASSETS-01`, §5).

**Not run this session, and not claimed as passed:** Electron (`npm test`,
`npm run build`) — the component did not change. No `flutter build`, no run on a
device or emulator. No live workload of any kind: no Ollama run, no A/B harness,
no completion-contract evaluation, no real mailbox.

**CI, read per job** (`gh run view 31205412539 --json jobs`, 2026-08-07): the
last pushed tip `5576a1d` is green in **all three** jobs — `python`, `electron`
and `mobile` all `success`. Read this session's own commits' CI live once pushed;
it is not predicted here.

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
- **`MOBILE-ASSETS-01` — a clean clone cannot build or test the mobile app.**
  `mobile/.gitignore` ignores `assets/fonts/*.ttf` and `flutter analyze` does not
  validate the pubspec `fonts:` section, so **a green `mobile` CI job is not
  evidence that the app builds anywhere.** It is now also the only thing between
  `flutter test` and CI (§7). See `mobile/assets/ASSETS_SETUP.md`.
- **`dev_verify.py` scope limits.** Its **Electron branch has still never run
  live** (the mobile branch has, §4). Cross-cutting widening covers
  `jarvis/graph/` and `jarvis/execution/` only; every other module relies on the
  direct filename match, the loose reference scan, or the full fallback. It
  deliberately does **not** select `flutter test` during iteration (§2, §7).
- **Mobile L3 confirmation: still no approve/deny UI.** `classifyChatChunk()`
  shows a neutral "not yet supported" note instead of raw JSON, but nothing
  resolves the interrupt; the graph stays interrupted server-side. This is now
  the largest functional gap on mobile.
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
- **Electron HUD confirmation is compile/parser-verified only — no live E2E.**
- **`python_run` is access-controlled, not sandboxed.**
- **Proactive turns gate L3 only**; an unwatched L2 write is mitigated by prompt
  instruction, not structurally closed.
- **Four `claude/*` scratch branches** hold commits unreachable from this branch
  (`eager-noether-46af01`, `gifted-wilbur-e021ea`, `stoic-spence-2c5246`,
  `thirsty-mclean-f67665`; counts last re-derived 2026-08-06). Do not delete
  without an explicit go-ahead.

## 6. Next engineering priority

**Completion-contract Finding 1 (source-substitution repair).** Binding the
repair's success criterion to the *requested* source, rather than "a chart
artifact exists somewhere", is the pilot's own recommended next single step and
is higher-value than further TTFB polish.

On mobile, the next real product gap is the **L3 approve/deny UI** (§5) — the
phone cannot complete any flow that reaches a confirmation. Wiring `flutter test`
into CI is *not* an engineering task until `MOBILE-ASSETS-01` is decided (§7).

Do not start any of this — or any product work — inside a session that is
closing.

## 7. Human-required actions

- **Google OAuth re-consent** (Gmail read, Calendar write, Contacts) — blocks the
  Faz 5 live measurement and the Faz 2 entity resolver.
- **Mobile font binaries** — owner deferred 2026-08-06. With `MOBILE-TEST-01`
  closed, this is now the *only* blocker to running `flutter test` in CI, so the
  decision has a concrete payoff it did not have before.
- **Should `flutter test` join `dev_verify.py`'s iteration loop?** Left
  unchanged deliberately (§2): the tool would then assume font assets on every
  checkout. Owner's call, not the fix's side effect.
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
- **This session's preflight warned "previous session did NOT run
  /session-close (exit: other)" — reconciled before any work started, and it was
  a false alarm.** `claude_session_state.py show` reported the marker as `state
  closed`, session `a291ca37…`, head `5576a1d8…`: the session that produced
  `5576a1d` did close cleanly. The breadcrumb belonged to a *later* session
  (`763bb06b…`, `reason: other`) that exited on the same HEAD with a clean tree
  and 0 ahead / 0 behind, so it had nothing to lose. Nothing was recovered
  because nothing was lost.
- This session ran `/session-close prepare` under its own identity, which
  overwrites the single marker file. The `a291ca37` closed record therefore
  survives only in this note.
- A next session whose preflight reports the previous session did not close, or
  whose SessionEnd identity was `UNVERIFIED`, reconciles before starting new
  work — and should check the marker itself before believing the warning.
