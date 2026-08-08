---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: eb598ce6e693859d1553b8bdb663d5fd9c43b035
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
- This session started at `6596335` and built **one work commit**, `eb598ce`,
  plus this closing documentation commit on top of it.
- Push state and CI for this session's own commits are **derived live**, never
  stored here:

```bash
git rev-list --left-right --count origin/langgraph-migration...HEAD
gh run list --branch langgraph-migration    # then: gh run view <id> --json jobs
```

- Push requires the owner's explicit in-chat approval, every session, every time.

## 2. Last completed work

**`eb598ce` — the L3 confirmation now lives on the screen the user can reach,
and the round-trip has run live on real hardware.**

`baf9f8b` wired the approve/deny card into `ChatScreen`. Nothing routes to
`ChatScreen`: `HomeShell`'s tabs are CORE/TASKS/SCHED/VAULT and CORE is
`HomeScreen`. So the feature passed its own tests while the shipped app could
not answer a prompt. `HomeScreen` also carried a **second, unmigrated SSE
reader** that only special-cased `[DONE]`, `[ERROR]` and a literal `{"async":`
prefix, so a `confirmation_required` frame fell through to `appendToLast()` —
rendered as raw JSON in the transcript, spoken aloud by TTS, and the interrupt
left stranded server-side until TTL. The server was never at fault: the frame on
the wire was well-formed and the gate held.

Three things a future reader would otherwise re-litigate:

- **One reader, or the bug returns.** `chat_sse.dart` exists precisely to stop
  ad-hoc prefix loops; it was written once and defeated by a *duplicate*. Chat,
  upload and confirmation continuation all go through `HomeScreen._consume()`
  now. A new surface routes here rather than adding a third loop.
- **`ChatScreen` was deleted, not kept as a reference** (854 lines). A second
  chat implementation is what made the first one's tests meaningless.
- **`HomeShell.screens` and `HomeScreenState` are public deliberately** — test
  seams for the two facts that had no coverage: that the chat surface is
  reachable, and that the upload leg uses the same reader (file_picker cannot be
  driven from a widget test). Production calls neither.

The `android.builtInKotlin` / `android.newDsl` lines in
`mobile/android/gradle.properties` are **not hand-authored**: they were reverted,
a full `flutter build apk --debug` was run, and Flutter's migrator re-added both.
Committed as required build metadata.

Details: [`.claude/rules/mobile.md`](.claude/rules/mobile.md).

## 3. Operational modes and rollout decisions

Defaults re-read from `jarvis/config.py` on 2026-08-08 (verify there, not here).
**This session changed no default and made no rollout decision.**

| setting | default | note |
|---|---|---|
| `required_outputs_mode` | `off` | Gate pre-registered and still unpassed; `object_created` delta and the corpus-B false-positive clauses remain the blockers. |
| `execution_contract_mode` | `shadow` | Honesty kernel. `enforce` gated on 100 real artifact operations with 0 reported false blocks. |
| `confirmation_gate_enabled` | `True` | The L3 gate is live in every interface, and mobile approve **and** deny are now live-verified on real hardware (§4). |
| `external_writes_enabled` | `True` | `--profile test` flips it off. |
| `monitor_proactive_enabled` | `False` | Proactive turns off by default. |
| `calendar_from_mail_enabled` | `False` | Faz 5; never run against a real mailbox. |
| `cloud_policy` / `local_model` | `off` / `qwen3:8b` | Local-only by default. |

Do not change a pre-registered threshold, corpus or metric after seeing a result.
**Mobile font binaries stay out of the repository** (owner decision 2026-08-06,
untouched); the consequence is `MOBILE-ASSETS-01` in §5.

**Local environment, not repository state:** the gitignored `.env` now sets both
`JARVIS_API_KEY` and `API_HOST=127.0.0.1`. These belong together —
`resolve_api_bind_host()` defaults to `0.0.0.0` as soon as a key is set, so the
explicit `API_HOST` is what keeps the API on loopback. The phone reaches it over
an `adb reverse tcp:8000 tcp:8000` USB tunnel, never the LAN. Exposing it is a
deliberate edit, not a side effect of having auth configured.

## 4. Tests and CI

Full verification of the tree of `eb598ce` — the last work commit, and the only
later change is this closing documentation commit — run through the recorder,
2026-08-08:

```powershell
.venv\Scripts\python.exe scripts\dev_verify.py --full
#   -> git diff --check clean; ruff All checks passed!
#      pytest -q -> 3450 passed, 5 deselected, 362 warnings (551.39s)
#      recorded at eb598ce6
```

3450 is **unchanged** from the previous snapshot: this session's work commit
touched `mobile/**` only, and `dev_verify.py`'s selector independently agreed —
"no changed file implies Python behaviour". The suite was run in full anyway
because the previous session's recorded evidence is `NOT REUSABLE` across
sessions, so nothing could be inherited.

Mobile, 2026-08-08, with the font assets present locally (`MOBILE-ASSETS-01`
means this is not reproducible on a clean clone):

```powershell
cd mobile; flutter analyze   # -> No issues found!
cd mobile; flutter test      # -> 47 passed  (was 37)
cd mobile; flutter build apk --debug   # -> built; installed with adb install -r
```

The 10 new tests are in `test/home_screen_confirmation_test.dart` and drive the
real `HomeScreen`. They are **falsifiable**: reinstating the pre-fix behaviour
turns 7 of the 10 red and leaves green exactly the three that probe unrelated
frame kinds (tab wiring, progress/final_answer, async-task).

**Live E2E, 2026-08-08 — real Galaxy S26 Ultra (`SM-S948B`), real server, real
model, `shell_run` (L3) as the probe.** Server on `127.0.0.1:8000` reached over
`adb reverse`; evidence is `data/audit_log.jsonl`:

| | approve | deny |
|---|---|---|
| before the tap | `confirm_required`, 0 executions, no file | `confirm_required`, 0 executions, no file |
| decision recorded | `user_approved` | `user_denied` |
| executions | **exactly 1** start + 1 end, `ok=True` | **0** |
| filesystem | probe file created | no file anywhere on disk |

The approve probe's contents were `APPROVED` plus the CRLF `Set-Content` itself
appends — reproduced byte-identically with a bare `Set-Content`, so nothing was
added by JARVIS. On both runs the card rendered the server's plain-language
description and **no frame internals** (`"type"`, `execution_id`, `args`,
`payload`) appeared on screen, verified by `uiautomator dump`.

**Correction kept visible:** `eb598ce`'s own commit message calls the device a
Galaxy S24 Ultra. That was wrong — the marketing name was inferred from the
`SM-S948B` model code and the inference was bad. The device is a **Galaxy S26
Ultra**. The message was left as written rather than amended, because the
recorded full-run evidence is keyed to that exact SHA.

A second `confirm_required` row appears one millisecond before each decision row.
That is the confirmation node re-recording its ruling on the resume pass
([`nodes.py:1805`](jarvis/graph/nodes.py:1805) writes a decision for every
risk ≥ 2 call each time the node runs), **not** a second prompt: no second card
appeared and the execution count is unchanged.

**Not run this session, and not claimed as passed:** Electron (`npm test`,
`npm run build`) — untouched. No Ollama A/B harness, no completion-contract
evaluation, no real mailbox.

**CI:** this session's commit is **not on origin**, so it has no CI result —
read it live after any push rather than predicting it here. The last judged tip
(`6596335`, run `31234178265`, 2026-08-08) was `success`; read it per job
(`gh run view <id> --json jobs`) rather than trusting the headline.

## 5. Known open issues

Each keeps its identifier; the detail stays in the linked document.

- **`MOBILE-16KB-01` — root cause binary-verified and fixed; live on-device
  re-confirmation still open.** The 2026-08-08 live E2E's dialog on the Galaxy
  S26 Ultra named two hard-failure libraries — `libonnxruntime.so` and
  `libonnxruntime4j_jni.so` — plus four compatibility warnings —
  `libflutter.so`, `libdartjni.so`, `libdatastore_shared_counter.so`,
  `libVkLayer_khronos_validation.so`.
  `feda49b` bumped `com.microsoft.onnxruntime:onnxruntime-android` `1.17.1` →
  `1.23.2` in [`mobile/android/app/build.gradle`](mobile/android/app/build.gradle)
  (upstream's 16 KB JNI linker fix, microsoft/onnxruntime PR #24947 / commit
  `8484199`, merged 2025-06-04, postdates 1.17.1). `gradlew app:dependencies`
  confirmed 1.17.1 was the sole resolved artifact before the bump and 1.23.2
  after, with no version conflict. Measured directly with `llvm-readelf -l`
  (NDK 28.2.13676358) on the extracted debug APK: both hard-failure libraries
  went from `Align 0x1000` (4 KB) at baseline to `Align 0x4000` (16 KB) after
  the bump, on **both** `arm64-v8a` and `x86_64`. `zipalign -c -P 16 -v 4` on
  the rebuilt APK also reports `Verification successful` for the page-aligned
  uncompressed `.so` entries.
  The four warning-only libraries were independently measured this session
  (same tool, same APKs) and are **already** `Align 0x4000`/`0x10000` — at
  baseline and after the bump alike — in this Flutter 3.44.6 / NDK
  28.2.13676358 toolchain. Static evidence only; not touched by this change.
  **Left open, not closed:** no ADB device was connected this session (`adb
  devices` empty throughout, checked at both the start and the end), so the
  actual acceptance test — the on-device dialog re-triggered against a real
  16 KB-page device, or confirmed gone — was never re-run. `getconf PAGE_SIZE`
  is still unread on any real device. §7 carries the follow-up.
  `flutter analyze`: no issues. `flutter test`: 47 passed, same count as the
  prior snapshot. `flutter build apk --debug`: builds.
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
- **`CI-FLAKE-CHROMA-01` — transient suspected, root cause unproven.**
  `chromadb>=0.6` is unpinned. Rerun a failed job **once** only when
  unexplainable by the diff; a passing rerun never proves a root cause.
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
  Mobile's equivalent gap is now closed (§4); Electron's is not.
- **`python_run` is access-controlled, not sandboxed.**
- **Proactive turns gate L3 only**; an unwatched L2 write is mitigated by prompt
  instruction, not structurally closed.
- **Four `claude/*` scratch branches** hold commits unreachable from this branch
  (`eager-noether-46af01`, `gifted-wilbur-e021ea`, `stoic-spence-2c5246`,
  `thirsty-mclean-f67665`; counts last re-derived 2026-08-06). Do not delete
  without an explicit go-ahead.

## 6. Next engineering priority

**`MOBILE-16KB-01`**'s binary-level fix is done (§5): `onnxruntime-android`
1.17.1 → 1.23.2 (`feda49b`), both previously-4 KB-aligned libraries now measure
16 KB-aligned on `arm64-v8a` and `x86_64`, `flutter analyze` / `flutter test` /
`flutter build apk --debug` all still pass. What is left is not engineering
work — it is reconnecting a device and re-triggering the cold-launch dialog to
confirm it live, tracked in §7.

After that, **completion-contract Finding 2** is the pilot's remaining open
finding, and its fix belongs to the next revision of the gate rather than to the
current one — do not redefine a pre-registered metric in place. `ROADMAP.md`'s
own next unstarted phase is **Faz 5 (proaktif mail → takvim)**, blocked on the
OAuth re-consent in §7.

The **Electron HUD confirmation round-trip is now the only interface whose gate
has never run live** — mobile's just did, and the same "2235 tests and 38/38
mutations, then 10/10 live failures" precedent applies to it.

Do not start any of this — or any product work — inside a session that is
closing.

## 7. Human-required actions

- **Reconnect the phone to confirm `MOBILE-16KB-01` live.** No ADB device was
  attached this session; the ONNX Runtime bump (1.17.1 → 1.23.2, `feda49b`) is
  binary-verified (§5) but the on-device 16 KB compatibility dialog has not been
  re-triggered since the fix landed, and `getconf PAGE_SIZE` is still unread on
  any real device.
- **Google OAuth re-consent** (Gmail read, Calendar write, Contacts) — blocks the
  Faz 5 live measurement and the Faz 2 entity resolver.
- **Mobile font binaries** — owner deferred 2026-08-06. This is the only blocker
  to running `flutter test` in CI, and it now guards 47 tests rather than 37.
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
- **The previous session closed cleanly** (marker `closed` at `6596335`), and
  this session's preflight said so — the false "did not close" warning that fired
  in the two sessions before this one did not recur.
- The gitignored `full-verification.json` holds this session's own reusable
  evidence, recorded at `eb598ce`. It is bound to a session id and a commit and
  is **not transferable** — this session correctly found the previous session's
  record `NOT REUSABLE` and re-ran the suite rather than inheriting it.
