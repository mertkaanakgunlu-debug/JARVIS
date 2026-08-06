---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 4806509f6bf11f0c2aafca3ba307994891285894
---

# HANDOFF — current state

Single-state snapshot, rewritten at each session close. Not a history: `git log`
and `CHANGELOG.md` own that. **If anything here contradicts the repository, the
repository is right and this file is the bug** — re-derive rather than trust.

Imported automatically by `CLAUDE.md`, so it is read every session. The
frontmatter above is a contract, not decoration: the SessionStart preflight
classifies this file from it. `covered_through_sha` names the last **work**
commit this snapshot describes — never this file's own closing commit.

## 1. Current verified state

- Branch: **`langgraph-migration`** (the active branch; `main` is a strict
  ancestor and behind).
- The branch tip at the start of this session was **`6bdd846`**
  (`docs: refresh handoff after lifecycle acceptance`), pushed, and read per job
  as CI run **`31064218844`**: `python` success, `electron` success, `mobile`
  **failure** — the `CI-MOBILE-01` signature.
- This session added two work commits (§2). Both are pushed, and CI run
  **`31068220833`** was read per job on their tip `4806509`: `python` success,
  `electron` success, **`mobile` success** — the first run in which `mobile`
  genuinely passed rather than being hidden by `continue-on-error`.
- On top of those sits this closing HANDOFF commit. **Its push state and CI
  outcome are not asserted here** — both change after this file is written.
  Derive them:

```bash
git rev-list --left-right --count origin/langgraph-migration...HEAD
gh run list --branch langgraph-migration     # then: gh run view <id> --json jobs
```

- Never quote how far `main` is behind. Derive it:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`

## 2. Last completed work

**`CI-MOBILE-01` cleared at the source.** The `mobile` job had been red since
long before any current work — 71 `flutter analyze` findings behind
`continue-on-error: true`, so the workflow headline stayed green while the job
stayed red. Nothing was suppressed to close it: no `analysis_options.yaml`, no
`ignore_for_file`, no `ignore:` comment added, and `.github/workflows/ci.yml`
untouched.

1. **`fix(mobile): clear the analyzer debt at the source`** (`383cd67`).
   69 × `deprecated_member_use`, each replacement checked against the
   SDK/package source: 65 × `withOpacity(x)` → `withValues(alpha: x)` (the
   deprecated method is literally `withAlpha((255.0 * opacity).round())`);
   1 × `Matrix4.scale(x, y, 1.0)` → `scaleByDouble(x, y, 1.0, 1.0)` (the fourth
   argument is the homogeneous `w` factor, and vector_math's own deprecated
   `scale` forwards `1.0` there); 1 × `Switch.activeColor` → `activeThumbColor`
   (`switch.dart:623,640` resolves `activeThumbColor ?? activeColor`, and the
   `Slider` in the same file keeps its own **non-deprecated** `activeColor`);
   2 × `listen(partialResults:)` → `listenOptions: SpeechListenOptions(...)`,
   compared field by field against the implicitly-built options object.

   Plus 2 × `asset_directory_does_not_exist`: the `pubspec.yaml` `assets:` block
   was dead in every direction and was removed. Nothing in `lib/` touches
   `rootBundle`/`DefaultAssetBundle`/`AssetManifest`; the `.ttf` files reach the
   app through the `fonts:` section instead; and the wake-word model is read by
   `WakeWordService.kt` via `assets.open("wake/…")`, the **Android** AssetManager
   root, which a Flutter declaration can never populate (Flutter packages under
   `flutter_assets/**`, `FlutterTaskHelper.kt:18`). That declaration never worked.

2. **`docs: record CI-MOBILE-01 as cleared, and retire its wave-through clause`**
   (`4806509`). `.claude/rules/mobile.md` inverted: the job is expected clean, so
   a failure is now a real regression. `.claude/skills/session-close/SKILL.md`
   lost the clause that let a `mobile` failure be reported non-blocking when it
   matched the CI-MOBILE-01 signature — an exemption with no referent is how a
   real failure gets waved through. `mobile/assets/ASSETS_SETUP.md` had two
   instructions that do not work (wake model in the wrong directory; "copy
   Orbitron-Regular over Orbitron-Black", which would have collapsed weight 800
   to Regular — the two files are distinct static instances, `Orbitron` vs
   `Orbitron ExtraBold`).

`dart format` was deliberately **not** run: this repo is not dart-format
formatted (4 of 5 untouched sample files would change), and formatting the 15
touched files would have rewritten 1482 lines around a 64-line change.

**Session Lifecycle acceptance fixes** (earlier, `dd41678` / `d606987` /
`1e1116e`): session identity is machine-authored and travels one way only from
the SessionStart payload; HANDOFF freshness is declared in frontmatter and
*counted* rather than inferred from the first hex token in the prose; the
mail→calendar fixture clock is frozen at `2026-08-04 12:00` Europe/Istanbul.

**Completion Contract Source Binding** (earlier, `a02d4be`): a source-bound
requirement must trace its artifact back to the source the user named, through
both the producing tool call's arguments and the working-set object's spec.
Non-repairable verdict `OUTPUT_SOURCE_MISMATCH`; shared identity in
`jarvis/execution/source_identity.py`.

## 3. Operational modes and rollout decisions

Defaults re-read from `jarvis/config.py` on 2026-08-06 (verify there, not here):

| setting | default | note |
|---|---|---|
| `required_outputs_mode` | `off` | Completion-contract pilot decision was **NO PROMOTION**; `off` is both the pre-pilot and current mode. There is no pre-registered `off → shadow` gate. |
| `execution_contract_mode` | `shadow` | Honesty kernel. `enforce` gated on 100 real artifact operations with 0 reported false blocks. |
| `confirmation_gate_enabled` | `True` | The L3 gate is live in every interface. |
| `external_writes_enabled` | `True` | `--profile test` flips it off. |
| `monitor_proactive_enabled` | `False` | Proactive turns off by default. |
| `calendar_from_mail_enabled` | `False` | Faz 5; never run against a real mailbox. |
| `cloud_policy` / `local_model` | `off` / `qwen3:8b` | Local-only by default. |

The completion-contract gate is **pre-registered and still unpassed**: the
`object_created` delta clause and the absolute 60 s first-visible latency clause
both failed. Nothing in this session re-ran or re-opened it. Do not change a
pre-registered threshold, corpus or metric after seeing a result.

**Mobile font binaries stay out of the repository** (owner decision,
2026-08-06). `mobile/.gitignore` keeps ignoring `assets/fonts/*.ttf`; the
consequence is recorded as an open issue in §5 rather than hidden.

## 4. Tests and CI

Run on **2026-08-06**, on the tree of `4806509` (the last work commit):

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests
#   -> All checks passed!
.venv\Scripts\python.exe -m pytest -q
#   -> 3269 passed, 5 deselected (474.76s)
git diff --check
#   -> clean
```

The full-suite figure is the **first** run on this tree: no failures, so nothing
was rerun and nothing is being reported behind a rerun.

Flutter, same date, same tree — the analyzer result is reported for **two**
layouts because they genuinely differ:

```powershell
flutter analyze          # local tree, Flutter 3.44.6
#   -> No issues found! (29.9s)
flutter analyze          # copy of exactly `git ls-files mobile` (76 files)
#   -> No issues found! (16.1s), exit code 0
flutter test
#   -> FAILS: "A Timer is still pending ..." -- pre-existing, see §5
flutter build apk --debug
#   -> COULD NOT RUN: "No Android SDK found" (flutter doctor: [X] Android toolchain)
```

The tracked-files-only copy exists because the local baseline is **not** CI's:
`flutter analyze` reported 69 findings here and 71 in CI, the two extra being
`asset_directory_does_not_exist` for directories that exist on this machine but
are not carried by git.

CI for `4806509`, read per job — run **`31068220833`**:

| job | id | conclusion |
|---|---|---|
| `python` | 92510446960 | success |
| `electron` | 92510446997 | success |
| `mobile` | 92510446998 | **success** — `No issues found! (ran in 9.6s)`, Flutter `stable-3.44.8` |

**CI for this closing commit is deliberately not predicted here.** Read it live
per job — a green workflow headline hides failing `continue-on-error` jobs:

```bash
gh run list --branch langgraph-migration
gh run view <id> --json jobs
```

## 5. Known open issues

- **`MOBILE-ASSETS-01` — a clean clone cannot build or test the mobile app.**
  `mobile/.gitignore` ignores `assets/fonts/*.ttf`, and `flutter analyze` does
  **not** validate the pubspec `fonts:` section — only `assets:`. So the analyzer
  is green while a tracked-files-only checkout dies at
  `unable to locate asset entry in pubspec.yaml: "assets/fonts/ShareTechMono-Regular.ttf"`
  → `Failed to build asset bundle`. CI only runs `analyze`, so **a green `mobile`
  job is not evidence that the app builds anywhere.** Owner decided 2026-08-06
  not to commit the font binaries; the fonts are SIL OFL (Share Tech Mono 1.003,
  Orbitron 2.001, read from the files' own name tables) so licensing is not the
  blocker — the decision is about binaries in the repo. See
  `mobile/assets/ASSETS_SETUP.md`.
- **`MOBILE-TEST-01` — `mobile/test/widget_test.dart` fails**, and did so before
  the CI-MOBILE-01 work: verified by running it against an unmodified
  `git archive` of the same HEAD. `_SplashRouterState.initState`
  (`mobile/lib/app.dart:67`) starts an uncancelled
  `Future.delayed(Duration(seconds: 2))`, so the test trips `'!timersPending'`.
  CI does not run `flutter test` for mobile.
- **Mobile `flutter analyze` runs with the DEFAULT analyzer rule set.**
  `flutter_lints` is a dev_dependency but is never included — there is no
  `analysis_options.yaml` anywhere in the repo. "0 findings" means 0 against the
  defaults, not against the `flutter_lints` ruleset.
- **CI's Flutter version is unpinned** (`subosito/flutter-action@v2`,
  `channel: stable`, no version). A new stable release can reintroduce
  deprecations and redden `mobile` with no code change — the same drift that
  broke the `python` job when ruff was unpinned. `ci.yml` was deliberately left
  untouched; pinning is an open option, not a decision.
- **`CI-FLAKE-CHROMA-01` — transient suspected, root cause unproven.** Runs have
  shown first-run failures with `chromadb ... no such table: acquire_write`
  across files a commit never touched; `chromadb>=0.6` is unpinned in
  `requirements.txt` (1.5.9 installed locally). Rerun a failed job **once** only
  when the failure is not explainable by the diff, never claim the rerun proved
  a root cause, and never classify a failure as this flake without reading its
  actual signature.
- **Source Binding scope limits** (documented, deliberate): a *bare*-filename
  request cannot disambiguate two same-named files in different directories;
  plain Unicode casefold does not equate Turkish `İ`/`i` across case; the
  pre-execution guard has **deterministic evidence only** — no live run has yet
  made the model attempt a wrong source, so it has never fired live.
- **Completion-contract latency**: treatment first-visible p90 ≈ 99.8 s against a
  60 s ceiling. Untouched by this session.
- Faz 5 (mail → calendar) is green on fixtures but **has never run against the
  real mailbox**; background ingestion stays off until it does.
- Electron HUD confirmation is compile/parser-verified only — **no live E2E**.
  The Flutter app renders **nothing** for confirmations; the server-side gate
  still holds, but an L3 flow is unusable from the phone.
- `python_run` is access-controlled, **not sandboxed** (no resource/network limit).
- Proactive turns gate **L3 only**; an unwatched L2 write is mitigated by prompt
  instruction, not structurally closed.
- Four `claude/*` scratch branches (the `.claude/worktrees/*` sessions) hold
  commits unreachable from this branch — re-derived 2026-08-06 as 5, 1, 7 and 1
  commits (`eager-noether-46af01`, `gifted-wilbur-e021ea`,
  `stoic-spence-2c5246`, `thirsty-mclean-f67665`). Two may be worth recovering.
  Do not delete without an explicit go-ahead.

## 6. Next engineering priority

**Completion-contract streaming / TTFB architecture.** With `CI-MOBILE-01`
closed, the latency clause is the one pre-registered gate clause still failing
(§5): treatment first-visible p90 ≈ 99.8 s against a 60 s ceiling. Either make a
contracted turn emit before the graph finishes, or accept the TTFB cost and
revise the ceiling *for a future gate* — never retroactively for the pilot
already run.

Second, and much smaller: `MOBILE-TEST-01`. It is one uncancelled timer, and it
is the only thing standing between `mobile` having a lint gate and `mobile`
having a lint gate plus a smoke test. Decide whether the fix belongs in the test
or in `app.dart` — a splash timer that outlives its widget is arguably the
product bug, not the test's.

Do not start either — or any product work — inside a session that is closing.

## 7. Human-required actions

- **Google OAuth re-consent** (Gmail read, Calendar write, Contacts) — blocks the
  Faz 5 live measurement and the Faz 2 entity resolver. The mail→Excel→chart
  chain is 10/10 on fixture data and has still never run against the real
  mailbox; the Gmail live test cannot start until this is done.
- **Mobile font binaries**: owner deferred on 2026-08-06 ("not now, separate
  decision"). Until it is made, `MOBILE-ASSETS-01` stays open and a fresh clone
  cannot build the app. Licensing is not the obstacle (SIL OFL, verified from the
  files themselves); the question is whether ~77 KB of binaries belong in the
  repository or the manual-setup workflow stands.

Push approval is per-session and per-action: it is requested in chat at the time
of the push, never recorded here.

## 8. Session recovery notes

- A **SessionStart** hook (`scripts/claude_session_start.py`) injects the
  repository preflight, so no orientation prompt needs pasting. It is fail-open:
  if it reports `SESSION PREFLIGHT DEGRADED`, re-derive state manually. Its
  **only** write is the gitignored `current.json` session-identity record.
- A **SessionEnd** hook writes `.claude/session-recovery/latest.json` (local,
  gitignored) on every exit, including `identity_status`. It never commits,
  pushes, or edits a tracked file.
- **Session identity is machine-authored.** Never infer a session id from a
  transcript filename, from "the newest file", or from memory, and never
  hand-write a recovery JSON file. Every transition goes through
  `scripts/claude_session_state.py` (`prepare` / `close` / `block` / `show`),
  which takes no id argument. If it refuses, report the refusal — do not route
  around it.
- `/session-close` owns closing: `prepare` verifies and commits without pushing;
  `finalize` pushes only on explicit approval and marks the state `closed`.
- A next session whose preflight says the previous one did **not** close, or
  whose SessionEnd identity was **UNVERIFIED**, should reconcile before starting
  new work.
- **The 2026-08-06 migration gap is closed and was reconciled, not ignored.** The
  session that built the machine-authored identity chain started before
  `current.json` existed, so it deliberately produced no marker for itself; this
  session's preflight therefore reported the previous session as unclosed, which
  was the documented expectation rather than a fault. `claude_session_state.py
  show` confirmed it: marker `closed` for session `772fdf77` at head `29f6995`,
  and the intervening session (`0e49f657`, `identity_status: matched`) exited
  without one. No recovery file was hand-written to paper over the gap.
