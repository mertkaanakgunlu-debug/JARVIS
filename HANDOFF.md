---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: e20065822f10d7df7a98e2b8a32b21d988af5d48
---

# HANDOFF — current state

Single-state snapshot, rewritten at each session close. Not a history: `git log`
and `CHANGELOG.md` own that. **If anything here contradicts the repository, the
repository is right and this file is the bug** — re-derive rather than trust.

Imported automatically by `CLAUDE.md`, so it is read every session. The
frontmatter above is a contract, not decoration — the SessionStart preflight
classifies this file from it. `covered_through_sha` names the last **work**
commit this snapshot describes — never this file's own closing commit.

## 1. Current verified state

- Branch: **`langgraph-migration`** (the active branch; `main` is a strict
  ancestor and behind).
- This session started at **`9421b3c`** (`docs: refresh handoff after CI
  recovery lifecycle hardening`), already pushed and clean. The prior
  session's own SessionEnd (`a85108b2…`) had not run `/session-close`, but
  its `identity_status` was `matched`, `head` equaled `9421b3c`, and
  `dirty_files` was empty — zero uncommitted work and zero unrecorded
  commits, i.e. a benign housekeeping gap, not a data-loss risk. Reconciled
  at this session's start; no action was needed beyond noting it.
- This session added **one work commit**, `e20065822f10d7df7a98e2b8a32b21d988af5d48`
  (`feat(streaming): give contracted+enforce turns a progress control
  frame`), on top of `9421b3c`. It is **NOT pushed** — derive live, never
  trust a stored number:

```bash
git rev-list --left-right --count origin/langgraph-migration...HEAD
gh run list --branch langgraph-migration     # then: gh run view <id> --json jobs
```

- **This session did not run `/session-close`.** The owner directed a manual
  flow instead: static diff review, quick checks, one work commit, this
  HANDOFF/CHANGELOG/eval-doc update, one closing documentation commit —
  explicitly **without** invoking `scripts/claude_session_state.py`. No
  `prepared`/`closed` marker was written for this session's identity by this
  work. A future preflight seeing no matching marker for this session is
  expected, not a bug — see §8.
- On top of the work commit sits this closing HANDOFF/CHANGELOG/eval-doc
  commit. **Its own SHA, push state and CI outcome are not asserted here** —
  all three are unknowable at the moment this file is written. Derive them
  with the same two commands above, against `HEAD` at the time of asking.
- **Neither commit has been pushed as of this writing.** Push needs the
  owner's explicit go-ahead, per standing protocol — not requested or given
  in this session yet.
- Never quote how far `main` is behind. Derive it:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`

## 2. Last completed work

**Completion-contract streaming/TTFB — a progress control frame for
contracted+`enforce` turns, live-validated, 2026-08-07, one work commit
(`e200658`, `feat(streaming): give contracted+enforce turns a progress
control frame`).**

**Root cause.** A buffered turn (`required_outputs_mode="enforce"` with a
resolved requirement) streamed nothing until the graph finished, by design —
a completion repair can replace an already-streamed draft, and voice cannot
un-speak a sentence. `chat_stream()`/`resume_and_stream()` accumulated every
delta into `chunks` but yielded none of them while buffered; the pilot's own
Finding 3 (`completion_contract_pilot_2026-08-05.md`) had already measured
the cost: treatment first-visible p90 99.78s, converging on total latency.

**Protocol.** One new internal marker, `__jarvis_progress__`
(`jarvis/voice/session.py`'s `parse_progress_marker()`/`describe_progress()`,
shaped like the existing `__jarvis_confirm__`/`__jarvis_final__` markers),
yielded exactly once — from inside the same
`try/except (asyncio.CancelledError, GeneratorExit)` block the streaming
loop already used, so a cancellation landing at that yield is covered by the
pre-existing bookkeeping — immediately before the buffered graph call in
both `chat_stream()` (`phase="preparing_required_output"`) and
`resume_and_stream()` (`phase="resuming_required_output"`). Carries only
`phase` and, when known, the requirement's `kind` (`required_outputs[0].get
("kind")` only — never the requirement dict as a whole, which can carry a
`source` sub-dict). Never joins `chunks`/`streamed_response`, so it cannot
reach history, memory, the run manifest or the output-contract classifier.
Reframed to a structured `{"type":"progress",...}` SSE frame in
`jarvis/api.py::_sse_frames()`; recognized and kept out of the visible
answer/TTS/history in Electron (`chatStream.js`'s `onProgress`, driving the
existing busy-surface labels — no new UI), the mobile app (new
`chat_sse.dart` classifier → the existing `_loadingNote` surface), and all
three voice call sites (`cli.py --voice`, `voice_api.py`, `voice/
session.py`'s `resolve_confirmation` — spoken as a short, deterministic,
non-success-claiming acknowledgement, kept out of persisted response text).

**Four pre-existing `__jarvis_final__` gaps found and fixed in the same
pass** (a correction marker — independent of buffering, fires on any turn a
critic/verification repair changes the answer — falling through unrecognized
and reaching the user as literal JSON): Electron's SSE parser had no
`final_answer` case at all (now **replaces** `out.text`, never appends);
`voice_api.py`'s `run_one_response()` and `voice/session.py`'s
`resolve_confirmation()` had no `__jarvis_final__` handling on their streams
(now swallowed, mirroring the one call site that already did this
correctly); `cli.py`'s text-mode confirmation resume printed it raw (now
replaces the accumulated draft — text can redraw, voice cannot). Mobile's
`chat_screen.dart` had the equivalent gap for both markers, closed by the
new classifier plus `TranscriptNotifier.replaceLast()`.

**Harness (`scripts/completion_contract_ab.py`), two rounds.** Round 1 added
`first_visible_kind`/`time_to_first_answer_token_s` — additive, reusing the
same marker parsers every real consumer uses, never replacing the
pre-registered `time_to_first_visible_s`. Round 2 followed a live run that
hit one trial resolving at `elapsed_s=4224.29` against a nominal 300s
`asyncio.wait_for` timeout: the underlying tool work had genuinely
succeeded (`object_created=true`), no answer token ever arrived, and the
pre-fix harness could not tell whether the excess ~3924s was the foreground
call's own slow cancellation or an **unbounded** `asyncio.gather()`
background-task drain. Fixed: `drain_background_tasks()` is now bounded
(`asyncio.wait(..., timeout=30.0)`, cancels and reports rather than waiting
forever, returns telemetry instead of `None`) and the row gained
`foreground_elapsed_s`/`cancellation_cleanup_s`/`background_drain_s`/
`background_drain_task_count`/`background_drain_timed_out` — all additive;
`elapsed_s`'s own formula/position is untouched. `report()` now prints three
per-arm views (`[all, incl. timeouts]` / `[completed only]` / `[timed-out]`)
so a stalled trial is never silently merged into, or silently dropped from,
the latency numbers. **`_gate_verdict()` itself: zero lines changed**,
pinned by a source-level test that asserts none of the new field names
appear in its body. The anomalous run (29/60 rows) was preserved, not
deleted or pooled — see the aborted-run section of the follow-up doc below.
**This is documented as a known eval-harness observability gap, not a
production blocker**: no file under `jarvis/` was touched by either harness
round, and the anomaly occurred after tool execution had already succeeded,
in a code path this work did not modify.

**Live validation** (staged: `--runs 1` → 6 trials clean → `--runs 3` → 18
trials clean; `--runs 10` deliberately not run — see the follow-up doc's
recommendation). Pooled, n=12/arm (matching the 2026-08-05 pilot's own
sample size): **0 timeouts, 0 errors, 0 background-drain events** across all
24 trials. Treatment first-visible p50/p90 **2.54s/2.57s** (`first_visible_
kind` = `progress`, 12/12) vs first-answer-token p50/p90 **53.36s/72.64s** —
the separation this work exists to produce, with zero exceptions across both
independent runs. `object_created` delta +1.7/10 against the pre-registered
+2/10 (consistent with the original pilot's +0.8/10 at this sample size).
Full numbers, the aborted run's exact anomaly-row fields, and proven-facts-
vs-hypotheses on the 4224s stall:
[`docs/eval/completion_contract_ttfb_followup_2026-08-07.md`](docs/eval/completion_contract_ttfb_followup_2026-08-07.md).
**Not a gate re-run**: the pre-registered gate
(`completion_contract_gate.md`) was not re-evaluated as an acceptance
decision, and the 2026-08-05 pilot document was not revised.

**Verification, run once, on the tree of `e200658`:**

```
ruff check jarvis scripts tests    -> All checks passed!
pytest -q                          -> 3375 passed, 5 deselected, 362 warnings, 619.19s
git diff --check                   -> clean
npm test (electron, vitest)        -> 32/32 passed (chatStream.test.js 13 -> 18)
npm run build (electron)           -> succeeds
flutter analyze                    -> No issues found!
flutter test (mobile)              -> +14 -1 (14 new pure-Dart tests; the 1 failure is
                                       the pre-existing MOBILE-TEST-01, unchanged)
```

## 3. Operational modes and rollout decisions

Defaults re-read from `jarvis/config.py` on 2026-08-07 (verify there, not
here) — **unchanged this session**:

| setting | default | note |
|---|---|---|
| `required_outputs_mode` | `off` | **Explicitly not promoted by this session's work.** The pre-registered gate was not re-run; corpus B/C were not measured; the 2026-08-05 pilot's failed clauses (Findings 1–2) are untouched. |
| `execution_contract_mode` | `shadow` | Honesty kernel. `enforce` gated on 100 real artifact operations with 0 reported false blocks. |
| `confirmation_gate_enabled` | `True` | The L3 gate is live in every interface. |
| `external_writes_enabled` | `True` | `--profile test` flips it off. |
| `monitor_proactive_enabled` | `False` | Proactive turns off by default. |
| `calendar_from_mail_enabled` | `False` | Faz 5; never run against a real mailbox. |
| `cloud_policy` / `local_model` | `off` / `qwen3:8b` | Local-only by default. |

The completion-contract gate is **pre-registered and still unpassed** for
promotion purposes: `object_created` delta and the false-positive/
`unexpected_chart_created` clauses (corpus B, not re-run this session)
remain the blockers. This session's live measurement (§2) narrowly
re-confirmed `object_created` delta (+1.7/10 at n=12, informational only —
not a gate re-run) and resolved the first-visible-latency ABSOLUTE clause
(now trivially passes, since a progress marker legitimately arrives fast) —
but a clause passing on a redefined-by-design measurement is not the same
as the promotion decision changing, and it has not changed. Do not change a
pre-registered threshold, corpus or metric after seeing a result.

**Mobile font binaries stay out of the repository** (owner decision,
2026-08-06, untouched this session). `mobile/.gitignore` keeps ignoring
`assets/fonts/*.ttf`; the consequence is recorded as an open issue in §5.

## 4. Tests and CI

Run on **2026-08-07**, on the tree of `e200658` (the last work commit,
committed but not pushed) — commands and results reproduced verbatim from
§2 above:

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests
#   -> All checks passed!
.venv\Scripts\python.exe -m pytest -q
#   -> 3375 passed, 5 deselected, 362 warnings (619.19s)
git diff --check
#   -> clean
```

3375 is up from the previous snapshot's 3326 by +49: +40 from the streaming/
progress-marker feature work (agent/API/voice/harness marker tests) and +9
from the harness round-2 phase-timing tests
(`tests/test_completion_contract_ttfb_metrics.py`). First run on this exact
tree — nothing was rerun, nothing is reported behind a rerun.

Electron (`electron/`): `npm test` (vitest) → **32/32 passed**
(`chatStream.test.js` 13 → 18 cases); `npm run build` → succeeds, no errors.

Mobile (`mobile/`): `flutter analyze` → **No issues found!** (CI-MOBILE-01
stays cleared — no new finding). `flutter test` (full directory) →
**+14 −1**: the 14 are new, isolated pure-Dart unit tests
(`chat_sse_test.dart`, `transcript_provider_test.dart` — neither pumps a
widget, so neither touches the pre-existing timer bug); the 1 failure is
`MOBILE-TEST-01`, confirmed byte-for-byte the same signature as before this
session (`!timersPending` at `binding.dart:2542`), unrelated and unchanged.

**No CI run exists yet for `e200658`** — it has not been pushed, so `push`
never fired for it. This is stated as an honest gap, not an unrun check
reported as passed:

```bash
gh run list --branch langgraph-migration
#   -> newest run is still 31117623901, against 08b15e0 (INCOMPLETE, see
#      the previous snapshot's §8 for its exact per-job signature — a
#      provider outage, not a code failure; untouched by this session)
```

Read this closing commit's own CI live, once it is pushed:

```bash
gh run list --branch langgraph-migration
gh run view <id> --json jobs
```

## 5. Known open issues

- **Completion-contract TTFB — narrowed, not closed.** This session gave a
  contracted+`enforce` turn a fast progress signal (first-visible p50 2.54s,
  live-validated, §2) and separated it from real answer latency in the
  harness's own metrics. It did **not** address the pilot's other two
  findings: Finding 1 (a completion repair can satisfy the contract from a
  *different* file than the one requested — the strongest argument against
  promotion) and Finding 2 (`honest_failure_retried`'s blind spot for a
  repair that "succeeds" via substitution). Both remain exactly as
  documented in `completion_contract_pilot_2026-08-05.md`.
- **`completion_contract_ab.py`'s 4224s anomaly — instrumented, not fully
  explained.** Proven: the underlying tool work succeeded
  (`object_created=true`) before ~3924s were spent somewhere between that
  success and the (never-reached) output-contract verification, and the
  pre-fix harness's background-task drain was unbounded. NOT proven: the
  exact mechanism (slow `asyncio` cancellation inside `graph.astream()` vs.
  a "thinking"-model generation that never goes idle long enough to trip a
  read timeout are both plausible, neither confirmed). Did not recur across
  24 further live trials in two independent clean runs. Known eval-harness
  observability gap, explicitly not a production blocker — no file under
  `jarvis/` was touched by the fix, and the stall occurred in a code path
  this session's product work did not modify. See the follow-up doc's §4
  for the full fact/hypothesis split.
- **`MOBILE-ASSETS-01` — a clean clone cannot build or test the mobile app.**
  `mobile/.gitignore` ignores `assets/fonts/*.ttf`, and `flutter analyze` does
  **not** validate the pubspec `fonts:` section — only `assets:`. So the analyzer
  is green while a tracked-files-only checkout dies at
  `unable to locate asset entry in pubspec.yaml: "assets/fonts/ShareTechMono-Regular.ttf"`
  → `Failed to build asset bundle`. CI only runs `analyze`, so **a green `mobile`
  job is not evidence that the app builds anywhere.** Owner decided 2026-08-06
  not to commit the font binaries; the fonts are SIL OFL so licensing is not the
  blocker — the decision is about binaries in the repo. See
  `mobile/assets/ASSETS_SETUP.md`.
- **`MOBILE-TEST-01` — `mobile/test/widget_test.dart` fails**, unchanged by
  this session (confirmed byte-for-byte same signature, §4).
  `_SplashRouterState.initState` (`mobile/lib/app.dart:67`) starts an
  uncancelled `Future.delayed(Duration(seconds: 2))`. CI does not run
  `flutter test` for mobile.
- **Mobile L3 confirmation: still no approve/deny UI.** `chat_screen.dart`'s
  new `classifyChatChunk()` (this session) recognizes a
  `confirmation_required` frame well enough to show a neutral "not yet
  supported" note instead of raw JSON — but nothing resolves it; the graph
  stays interrupted server-side exactly as before. Building the UI was
  explicitly out of scope this session.
- **Mobile `flutter analyze` runs with the DEFAULT analyzer rule set** — no
  `analysis_options.yaml` anywhere in the repo, so `flutter_lints` (a
  dev_dependency) is never actually applied.
- **CI's Flutter version is unpinned** (`subosito/flutter-action@v2`,
  `channel: stable`, no version) — a new stable release can redden `mobile`
  with no code change.
- **`CI-FLAKE-CHROMA-01` — transient suspected, root cause unproven.**
  `chromadb>=0.6` is unpinned in `requirements.txt`. Rerun a failed job
  **once** only when unexplainable by the diff; never claim the rerun proved
  a root cause.
- **A lost CI verdict (`CI_INFRA_UNAVAILABLE`) has no recovery mechanism
  beyond "the next push judges the next tip."** Documented, deliberate limit
  (`workflow_dispatch` cannot work on this repo's default-branch layout).
- **`scripts/claude_session_state.py`'s marker structural check does not
  validate field CONTENT, only presence/coarse type.** Deliberate scope
  limit from a prior session's round 3.
- **Source Binding scope limits** (documented, deliberate): bare-filename
  ambiguity across directories; plain Unicode casefold on Turkish İ/i;
  pre-execution guard has deterministic evidence only.
- Faz 5 (mail → calendar) is green on fixtures but **has never run against
  the real mailbox**; background ingestion stays off until it does.
- Electron HUD confirmation is compile/parser-verified only — **no live
  E2E**. The Flutter app renders **nothing** for confirmations (see above).
- `python_run` is access-controlled, **not sandboxed**.
- Proactive turns gate **L3 only**; an unwatched L2 write is mitigated by
  prompt instruction, not structurally closed.
- Four `claude/*` scratch branches hold commits unreachable from this
  branch — last re-derived 2026-08-06 as 5, 1, 7 and 1 commits
  (`eager-noether-46af01`, `gifted-wilbur-e021ea`, `stoic-spence-2c5246`,
  `thirsty-mclean-f67665`). Do not delete without an explicit go-ahead.

## 6. Next engineering priority

**`MOBILE-TEST-01`.** One uncancelled timer
(`_SplashRouterState.initState`, `mobile/lib/app.dart:67`), and it is the
only thing standing between `mobile` having a lint gate and `mobile` having
a lint gate plus a smoke test. Decide whether the fix belongs in the test or
in `app.dart` — a splash timer that outlives its widget is arguably the
product bug, not the test's.

**If the completion-contract line of work is picked back up**, the pilot's
Finding 1 (source-substitution repair) is the higher-value next step over
further TTFB polish: it is the strongest documented argument against ever
promoting `required_outputs_mode` past `off`, and this session's work did
not touch it. Binding the repair's success criterion to the requested
source (not just "a chart artifact exists somewhere") is the pilot's own
recommended next single step.

Do not start either — or any product work — inside a session that is
closing.

## 7. Human-required actions

- **Google OAuth re-consent** (Gmail read, Calendar write, Contacts) — blocks the
  Faz 5 live measurement and the Faz 2 entity resolver. Unchanged.
- **Mobile font binaries**: owner deferred on 2026-08-06. Until decided,
  `MOBILE-ASSETS-01` stays open.
- **Default-branch / `.github/` layout**: the repo's default branch (`main`)
  carries no `.github/` directory. Flagged for the owner to decide whether
  it is worth changing, not decided on their behalf. Unchanged.

Push approval is per-session and per-action: it is requested in chat at the time
of the push, never recorded here.

## 8. Session recovery notes

- A **SessionStart** hook (`scripts/claude_session_start.py`) injects the
  repository preflight; fail-open (`SESSION PREFLIGHT DEGRADED` → re-derive
  manually). Its **only** write is the gitignored `current.json` identity
  record.
- A **SessionEnd** hook writes `.claude/session-recovery/latest.json` on
  every exit, including `identity_status`. Never commits, pushes, or edits a
  tracked file.
- Session identity is machine-authored; every lifecycle transition normally
  goes through `scripts/claude_session_state.py` (`prepare`/`close`/
  `block`/`show`), which takes no id argument and refuses rather than being
  routed around.
- **This session deliberately did not call that script.** The owner directed
  a manual close-out (this HANDOFF update + one closing documentation
  commit) instead of `/session-close`, explicitly withholding push approval
  pending review of this report. Consequence for the next preflight: there
  is **no fresh `prepared`/`closed` marker for this session's identity** —
  the most recent marker on disk is still the one from the session that
  produced `9421b3c` (state `closed`, matching `9421b3c`, not this
  session's `e200658`/closing-commit tip). This is an intentional deviation
  from the standard protocol, not a dropped step — if a normal
  `/session-close` is wanted for this work, it has not run yet.
- A next session (or a continuation of this one) whose preflight reports the
  previous session did **not** close, or whose SessionEnd identity was
  **UNVERIFIED**, should reconcile before starting new work — for this
  specific gap, reconciliation is simply reading this note: no work was
  lost, nothing is uncommitted beyond what this closing commit is about to
  capture, and the marker mismatch is expected until/unless `/session-close`
  is actually run.
