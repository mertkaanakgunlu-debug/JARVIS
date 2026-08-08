---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 41651b4597c17d38a7ff93357dd2e59a314466a9
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
- `8602a0b` (`CI-FLAKE-CHROMA-01`) and `41651b4` (its closing docs) are
  **pushed and CI-confirmed**: run `31274352271`, `python`/`electron`/`mobile`
  all `success` at job level, the `python` job's full log carries zero
  `acquire_write`/`no such table` occurrences (was the previous push's
  `python`-job failure signature, run `31270921707`).
- This chapter started at `41651b4` and made **no code change** — only a
  live-device confirmation (§2) — so it produces one closing documentation
  commit on top of it.
- Push state and CI for this document's own closing commit are **derived
  live**, never stored here:

```bash
git rev-list --left-right --count origin/langgraph-migration...HEAD
gh run list --branch langgraph-migration    # then: gh run view <id> --json jobs
```

- Push requires the owner's explicit in-chat approval, every session, every time.

## 2. Last completed work

**`8602a0b` — `CI-FLAKE-CHROMA-01` root-caused and fixed: chromadb's
`SharedSystemClient` was caching its process-global `System` on the literal,
unresolved `persist_directory` string.**

The previous session's push (`c081cee`) came back `CI_BLOCKING_FAILURE`: run
`31270921707`'s `python` job failed 15 tests, all on one xdist worker (`gw0`),
each `chromadb.errors.InternalError: ... no such table: acquire_write`, each
with a different `isolated_cwd`/`tmp_path` directory but the identical
underlying `chromadb.api.rust.RustBindingsAPI` instance address across
unrelated tests' tracebacks. That last fact was the actual lead:
`chromadb/api/shared_system_client.py` keys its cache with
`identifier = settings.persist_directory` — no `abspath`/`realpath` of its
own. `jarvis/paths.py`'s `jarvis_home()` defaults to `Path(".")`, so
`Memory`'s default `chroma_dir` stayed the literal relative string
`"data/chroma"` — byte-identical across every cwd. Two `Memory()`s built in
physically distinct directories within the same pytest-xdist worker process
(exactly what `-n 4 --dist load` does at scale) silently shared one chromadb
System.

Proven, not assumed, before any fix landed: a same-process, two-cwd repro
(`Memory` A writes a record, `Memory` B — a different physical directory,
same default settings — reads it back via `.count()`, never having written
anything itself) reproduced the collision every time pre-fix and never
post-fix; the two new regression tests built from that repro fail on the
pre-fix code (verified via a temporary `git stash` of just the fix) and pass
after it.

Two things a future reader would otherwise re-litigate:

- **This is not the same bug the 2026-07-23 review already fixed.** That
  review found two `Memory()`s in ONE test sharing an identical (deliberately
  cwd-relative) `chroma_dir` and fixed it two ways: per-arm workspace-scoped
  dirs at that call site, and a general `Memory.close()` for the refcount
  leak. Both were real, but neither touched the *default* path's identity —
  so the identical mechanism resurfaced across DIFFERENT tests once enough of
  them (927 → 3450) shared xdist workers. The fix here is scoped to
  `Memory.__init__`'s one call site (`self._chroma_dir.resolve()` right
  before `PersistentClient` sees it) — not `paths.resolve()` generally, which
  vault_dir and other stores also use and which this task deliberately left
  alone.
- **`Memory.close()` existed but was called almost nowhere.** Production
  never called it (the process exiting was assumed to reclaim it — still
  true), and only ~9 of ~32 Memory-constructing test files called it
  themselves, leaking hundreds of chromadb Systems across a 3450-test run
  even with the identity fix in place. Wired into the real CLI (`_run_loop`,
  the voice loop) and API (`lifespan` shutdown) exit paths for defense in
  depth, and `tests/conftest.py` gained an autouse `_close_memory_instances`
  fixture that closes every `Memory` a test builds — directly or via
  `JarvisAgent` — without editing the individual test files.

Verification: targeted serial (126 passed) and **7 independent**
`-n 4 --dist load` runs of the exact 6 files CI's 15 failures came from, all
green; the canonical `dev_verify.py --full` (§4); and, separately, **two**
full-suite `pytest -n 4 --dist load` runs — CI's exact command — both green,
where the un-fixed tree failed. `CI-FLAKE-CHROMA-01` is closed as a known
issue (was in §5 as "transient suspected, root cause unproven" — that framing
was wrong; the root cause was fully deterministic, not transient).

**`MOBILE-16KB-01` — the live on-device acceptance test finally ran, and
closes the issue.** No code changed. `feda49b`'s binary fix (onnxruntime
1.17.1 → 1.23.2) was already alignment-verified statically; what was missing
was ever re-triggering the actual cold-launch dialog on real hardware. Device:
the same Galaxy S26 Ultra, `SM-S948B` — `adb devices` needed an on-device USB
debug re-authorization first (was `unauthorized`, not absent). Newly read this
chapter, on real hardware, for the first time in this project's history:
`getconf PAGE_SIZE` → **4096** (this device runs 4 KB pages, not 16 KB — the
compatibility warning Android showed pre-fix is a static native-library
alignment check, not evidence the device itself runs 16 KB pages). The debug
APK was rebuilt from the unchanged `feda49b` tree, `zipalign -c -P 16`
re-confirmed `Verification successful` on the fresh build, installed with
`adb install -r` (existing app data untouched), then force-stopped and
cold-launched. Four independent, multi-modal checks agree: a screenshot shows
the normal CORE screen with no overlay; `dumpsys activity` reports
`topResumedActivity=...com.mertkaan.jarvis/.MainActivity` (no system dialog
activity in front); the full post-launch `logcat` capture (10156 lines) has
zero `onnxruntime`/`16 ?kb`/`page.?size`/`compat` matches on the app's own
lines, only a clean launch sequence; and a `uiautomator dump` of the live UI
tree has exactly one `package` value (`com.mertkaan.jarvis`) and zero
compatibility-related text anywhere in it. The dialog is gone.

**Residual limitation, stated plainly: no session has produced runtime
execution evidence on an actual 16 KB-page kernel.** This device is a 4 KB
device (`getconf PAGE_SIZE=4096`, above) — closure rests on two things only:
(1) the binary ELF/APK alignment fix, verified statically with
`llvm-readelf`/`zipalign` (§5's old entry, now dropped), and (2) this same
physical device's Android compatibility warning no longer appearing with the
new APK. Neither is "tested on a 16 KB device." If a genuine 16 KB-page
device or emulator ever becomes available, that is a stronger acceptance test
this project has still never run. Closed on the evidence above regardless —
the compatibility warning Android showed pre-fix was itself never a live
16 KB-kernel test either (same 4 KB device, same static alignment check,
before vs. after), so this closure is symmetric with what opened the issue,
not a weaker bar applied only at the end.

## 3. Operational modes and rollout decisions

Defaults re-read from `jarvis/config.py` on 2026-08-08 (verify there, not here).
**This session changed no default and made no rollout decision.**

| setting | default | note |
|---|---|---|
| `required_outputs_mode` | `off` | Gate pre-registered and still unpassed; `object_created` delta and the corpus-B false-positive clauses remain the blockers. |
| `execution_contract_mode` | `shadow` | Honesty kernel. `enforce` gated on 100 real artifact operations with 0 reported false blocks. |
| `confirmation_gate_enabled` | `True` | The L3 gate is live in every interface; mobile approve **and** deny are live-verified on real hardware (untouched this session — see prior evidence, §4). |
| `external_writes_enabled` | `True` | `--profile test` flips it off. |
| `monitor_proactive_enabled` | `False` | Proactive turns off by default. |
| `calendar_from_mail_enabled` | `False` | Faz 5; never run against a real mailbox. |
| `cloud_policy` / `local_model` | `off` / `qwen3:8b` | Local-only by default. |

Do not change a pre-registered threshold, corpus or metric after seeing a result.
**Mobile font binaries stay out of the repository** (owner decision 2026-08-06,
untouched); the consequence is `MOBILE-ASSETS-01` in §5.

**Local environment, not repository state:** the gitignored `.env` sets both
`JARVIS_API_KEY` and `API_HOST=127.0.0.1`. These belong together —
`resolve_api_bind_host()` defaults to `0.0.0.0` as soon as a key is set, so the
explicit `API_HOST` is what keeps the API on loopback. The phone reaches it over
an `adb reverse tcp:8000 tcp:8000` USB tunnel, never the LAN. Exposing it is a
deliberate edit, not a side effect of having auth configured.

## 4. Tests and CI

**Python, this session, run through the recorder on `8602a0b`** (the only work
commit; this closing commit changes documents only):

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests    # -> All checks passed!
.venv\Scripts\python.exe scripts\dev_verify.py --full
#   -> git diff --check clean
#      pytest -q -> 3454 passed, 5 deselected, 0 failed, 390 warnings (603.84s)
#      recorded at 8602a0b7
```

3454 vs. the prior snapshot's 3450 is exactly the **+4** new regression tests
in `tests/test_memory_lifecycle.py` (distinct-cwd identity, no cross-cwd leak,
same-path reuse unchanged, the autoclose mechanism itself) — nothing else in
the diff touches test collection.

**The acceptance bar for this task was CI's own exact command, not the
serial suite** — the un-fixed tree passed `pytest -q` locally while
`pytest -n 4 --dist load` failed in CI, so serial-green was never going to be
convincing on its own. Run twice, full suite, both green:

```powershell
.venv\Scripts\python.exe -m pytest -n 4 --dist load
#   run 1 -> 3454 passed, 395 warnings in 216.09s (0:03:36)
#   run 2 -> 3454 passed, 395 warnings in 242.60s (0:04:02)
```

Plus **7 independent** `-n 4 --dist load` runs (26.71s–30.94s each) scoped to
the exact 6 files CI's 15 failures came from (`test_alpha_capabilities.py`,
`test_output_contract_graph.py`, `test_plot_inline.py`,
`test_prepare_execution_node.py`, `test_procedure_store.py`,
`test_todo_bg_analysis.py`) — 126/126 every time, no retries, no reruns hiding
a failure.

**Mobile, this chapter — live device, no code change:** `flutter build apk
--debug` on the unchanged `feda49b` tree, `zipalign -c -P 16` re-confirmed
`Verification successful`, `adb install -r` onto the real Galaxy S26 Ultra
(`SM-S948B`) preserving app data, cold-launch verified dialog-free — full
evidence and the surprising `getconf PAGE_SIZE=4096` reading are in §2.
`flutter analyze`/`flutter test` were **not** rerun this chapter (no code
changed since the prior snapshot's run on this same `feda49b` tree). Electron
(`npm test`/`npm run build`) untouched. No Ollama A/B harness, no
completion-contract evaluation, no real mailbox.

**CI:** the last **judged** tip on origin is now `41651b4` (also the tip when
this document was written, current `origin/langgraph-migration`) — run
`31274352271`, **`success`** at job level for `python`, `electron`, and
`mobile` alike; the `python` job's full log (1048 lines) carries zero
`acquire_write`/`no such table` occurrences, and its pytest summary line reads
`3453 passed, 1 skipped` (3454 selected, 0 failed) in `297.94s`, matching the
local `-n 4 --dist load` evidence above. `CI-FLAKE-CHROMA-01`'s acceptance CI
is this run. Read future commits' CI live, per job
(`gh run view <id> --json jobs`), never from the workflow headline.

## 5. Known open issues

Each keeps its identifier; the detail stays in the linked document.
`CI-FLAKE-CHROMA-01` and `MOBILE-16KB-01` are **closed** (§2) and removed from
this list; neither is relabelled here, only dropped, per the doc rule.

- **Mobile confirmation: no cross-tab indicator, and no `conversation_id`.** A
  prompt raised while the user is on another tab is answerable when they return
  (the provider is app-scoped) but nothing signals it from elsewhere. Now that
  the card lives on the CORE tab this is narrower than it was, but not closed.
  `confirmStream()` sends no `conversation_id`, matching `chatStream()`; if
  mobile ever pins a conversation, both must change together or the resume is
  refused server-side.
- **Completion-contract Finding 2 — open.** `honest_failure_retried` counts
  eligible rows that carry `repair_attempted`, so a repair that fires on an
  honest failure and then produces *something* leaves the eligible set and is
  never counted. Left exactly as pre-registered — a note for the next revision of
  the gate, not a change to this one. Finding 1 is **closed** (`a02d4be`);
  Finding 3 (TTFB) was narrowed by `e200658`, not closed. See
  [`docs/eval/completion_contract_pilot_2026-08-05.md`](docs/eval/completion_contract_pilot_2026-08-05.md).
- **`completion_contract_ab.py`'s 4224s anomaly — instrumented, not explained.**
  A known eval-harness observability gap, explicitly not a production blocker;
  the proven-fact vs. hypothesis split is in
  [`docs/eval/completion_contract_ttfb_followup_2026-08-07.md`](docs/eval/completion_contract_ttfb_followup_2026-08-07.md).
- **`MOBILE-ASSETS-01` — a clean clone cannot build or test the mobile app.**
  `mobile/.gitignore` ignores `assets/fonts/*.ttf` and `flutter analyze` does not
  validate the pubspec `fonts:` section, so **a green `mobile` CI job is not
  evidence that the app builds anywhere.** It is also the only thing between
  `flutter test` and CI (§7). See `mobile/assets/ASSETS_SETUP.md`.
- **Wake-word asset is absent from this checkout.**
  `android/app/src/main/assets/wake/hey_jarvis_v0.1.onnx` is gitignored and not
  on disk. It does not block a build (wake-word defaults off) but the feature
  fails at runtime if enabled.
- **`dev_verify.py` scope limits.** Its **Electron branch has still never run
  live**. Cross-cutting widening covers `jarvis/graph/` and `jarvis/execution/`
  only. It deliberately does **not** select `flutter test` during iteration (§7).
- **Mobile `flutter analyze` runs with the DEFAULT analyzer rule set** — no
  `analysis_options.yaml` anywhere, so `flutter_lints` is never applied.
- **CI's Flutter version is unpinned** (`subosito/flutter-action@v2`,
  `channel: stable`) — a new stable release can redden `mobile` with no code
  change.
- **`chromadb>=0.6` is still unpinned in `requirements.txt`.** Not itself the
  cause of `CI-FLAKE-CHROMA-01` (that was this codebase's own unresolved
  persist-path identity, §2) but an upstream version bump remains untested
  against the fix here until it happens.
- **A lost CI verdict (`CI_INFRA_UNAVAILABLE`) has no recovery mechanism** beyond
  "the next push judges the next tip" — a documented, deliberate limit
  (`workflow_dispatch` cannot work on this repo's default-branch layout).
- **`claude_session_state.py`'s marker structural check validates presence and
  coarse type, not field CONTENT** — a deliberate scope limit.
- **Source Binding scope limits** (deliberate): a bare-filename request is
  satisfied by a same-named file in a different directory; plain Unicode
  casefold on Turkish İ/i; pre-execution guard has deterministic evidence only.
- **Faz 5 (mail → calendar) has never run against the real mailbox** — green on
  fixtures only; background ingestion stays off until it does.
- **Electron HUD confirmation is compile/parser-verified only — no live E2E.**
  Mobile's equivalent gap is closed.
- **`python_run` is access-controlled, not sandboxed.**
- **Proactive turns gate L3 only**; an unwatched L2 write is mitigated by prompt
  instruction, not structurally closed.
- **Four `claude/*` scratch branches** hold commits unreachable from this branch
  (`eager-noether-46af01`, `gifted-wilbur-e021ea`, `stoic-spence-2c5246`,
  `thirsty-mclean-f67665`; counts last re-derived 2026-08-06). Do not delete
  without an explicit go-ahead.

## 6. Next engineering priority

`CI-FLAKE-CHROMA-01` and `MOBILE-16KB-01` are both closed and pushed (§1, §2) —
nothing left on either.

**Completion-contract Finding 2** is the pilot's remaining open
finding, and its fix belongs to the next revision of the gate rather than to the
current one — do not redefine a pre-registered metric in place. `ROADMAP.md`'s
own next unstarted phase is **Faz 5 (proaktif mail → takvim)**, blocked on the
OAuth re-consent in §7.

The **Electron HUD confirmation round-trip is now the only interface whose gate
has never run live** — mobile's already did, and the same "2235 tests and 38/38
mutations, then 10/10 live failures" precedent applies to it.

Do not start any of this — or any product work — inside a session that is
closing.

## 7. Human-required actions

- **Google OAuth re-consent** (Gmail read, Calendar write, Contacts) — blocks the
  Faz 5 live measurement and the Faz 2 entity resolver.
- **Mobile font binaries** — owner deferred 2026-08-06. This is the only blocker
  to running `flutter test` in CI, and it guards 47 tests.
- **Should `flutter test` join `dev_verify.py`'s iteration loop?** Left unchanged
  deliberately: the tool would then assume font assets on every checkout.
- **Default-branch / `.github/` layout** — `main` carries no `.github/`
  directory. Flagged for the owner to decide, not decided on their behalf.
- **Android SDK is now installed on this machine** (Temurin JDK 21, cmdline-tools,
  platform 36, build-tools 36.0.0, at `%LOCALAPPDATA%\Android\sdk`). It was absent
  before 2026-08-08, and `mobile/android/local.properties` — gitignored — had
  stale `sdk.dir`/`java.home` paths pointing at software that was never installed
  here. A different machine will hit the same wall.

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
- **This session inherited a `blocked` marker from a DIFFERENT, earlier
  session** (`b9b95e68`, `CI_BLOCKING_FAILURE`, run `31270921707`, job
  `python`) — the correct outcome of that session's own close attempt, not
  relabelled here. Between that session and this one, a third session
  (`bb8cf67a`) started and exited (`reason: other`) with **no commits and no
  dirty files** — reconciled at this session's start as nothing-to-recover,
  not a lost-work case. This session prepares under its own identity
  (`0e05d735`), which the helper confirmed differs from the blocked marker's
  owner.
- The gitignored `full-verification.json` holds this session's own reusable
  evidence, recorded at `8602a0b7` (superseding the prior snapshot's
  `2d4392a8` record — different session, this session's own work commit). It
  is bound to a session id and a commit and is **not transferable** —
  `claude_session_state.py verification` confirms `REUSABLE` for this
  session's own record; a future session must re-run rather than inherit it.
  Still current: this chapter made no code change, so no new full run was
  needed or recorded.
- **Session `0e05d735` closed once already** (marker `closed` at `41651b45`,
  after `8602a0b`/`CI-FLAKE-CHROMA-01` pushed and CI-confirmed) and then did
  one more chapter of work — the `MOBILE-16KB-01` live confirmation this
  document describes — under the **same** session identity, per the owner's
  own follow-up prompt in the same conversation rather than a fresh
  `SessionStart`. `prepare`/`close` do not forbid re-preparing a session whose
  marker is `closed` (only a `blocked` marker is refused), so this is a
  legitimate second `prepare → close` cycle, not a protocol violation — but a
  future preflight seeing a `closed` marker whose `head` is behind the actual
  tip should read this bullet before assuming something is wrong.
