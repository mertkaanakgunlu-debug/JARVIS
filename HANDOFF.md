---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 77b33d6eef930c9dbaa54063a396c57a6cf2d15a
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
- This session started at `0356120` and built **three work commits**, plus this
  closing documentation commit on top of them. The chain, which does not change,
  is `77b33d6 ← baf9f8b ← 7d83d27 ← 0356120`.
- Push state and CI for this session's own commits are **derived live**, never
  stored here:

```bash
git rev-list --left-right --count origin/langgraph-migration...HEAD
gh run list --branch langgraph-migration    # then: gh run view <id> --json jobs
```

- Push requires the owner's explicit in-chat approval, every session, every time.

**Two documents were wrong and are now corrected — believe the code, not an
older copy of this file.** `HANDOFF.md` claimed completion-contract Finding 1
was open; `a02d4be` (2026-08-05) closed it. `ROADMAP.md` claimed two
`tool_router.py` pattern gaps were found-but-unfixed; `00ba15c` (2026-08-01)
closed both. Neither was re-implemented. Historical eval documents were left
exactly as written — they were accurate on their own dates.

## 2. Last completed work

Three commits, 2026-08-08.

**`7d83d27` — the completion contract's finding-1 *shapes* are now pinned.**
Source Binding already refuses a chart drawn from a file other than the one the
user named, and its tests cover each leg of that check in isolation. What had no
test was either shape the 2026-08-05 pilot actually produced, both of which
reach `classify()` as an ordinary turn: a repair round that substitutes another
file after the requested source failed honestly, and a substitution laundered
through `plot_data(data_json=...)`, which carries no wrong path to point at. The
second matters because the pre-execution guard that already refuses it runs in
`enforce` only — in `shadow`, where a pilot observes, `classify()` is the sole
defence. Both are falsifiable: dropping the requirement's `source` flips each to
`SATISFIED`, the pre-fix behaviour. Tests only.

**`baf9f8b` — an L3 confirmation can be answered from the phone.** The gate
always held server-side, but mobile rendered no approve/deny UI, so any flow
reaching a gated action was unusable there. `POST /chat/confirm/{id}` now joins
the two SSE endpoints the client had, and its continuation is driven through the
**same** reader as a new turn (`_consume()`): the server wraps both through
`_sse_frames()`, so the continuation can carry tokens, a `final_answer`, a
progress marker and a *second* `confirmation_required`. Three decisions a future
reader would otherwise re-litigate: the prompt lives in an app-scoped provider
because the stream delivering it ends immediately and a widget-local copy would
not survive a rebuild; the WS `confirmation_required` broadcast feeds the same
provider as a second leg, which is what recovers a prompt whose SSE stream died
and the only leg that delivers one raised on another transport; and the card
shows `policy_guard.describe_call`'s plain-language line, degrading to the tool
NAME and never to `args` — an args fallback would put raw JSON back on screen.
Double-submit, stale-tap and transport-failure handling live in
`ConfirmationNotifier`, not in the buttons. Details:
[`.claude/rules/mobile.md`](.claude/rules/mobile.md).

**`77b33d6` — `.claude/rules/mobile.md` no longer contradicts the code.** It
auto-loads for anything under `mobile/` and still said the app renders nothing
for a confirmation prompt.

## 3. Operational modes and rollout decisions

Defaults re-read from `jarvis/config.py` on 2026-08-08 (verify there, not here).
**This session changed no default and made no rollout decision.**

| setting | default | note |
|---|---|---|
| `required_outputs_mode` | `off` | Gate pre-registered and still unpassed; `object_created` delta and the corpus-B false-positive clauses remain the blockers. Finding 1 being closed does not retry the gate. |
| `execution_contract_mode` | `shadow` | Honesty kernel. `enforce` gated on 100 real artifact operations with 0 reported false blocks. |
| `confirmation_gate_enabled` | `True` | The L3 gate is live in every interface, and **mobile can now answer it** (§2). |
| `external_writes_enabled` | `True` | `--profile test` flips it off. |
| `monitor_proactive_enabled` | `False` | Proactive turns off by default. |
| `calendar_from_mail_enabled` | `False` | Faz 5; never run against a real mailbox. |
| `cloud_policy` / `local_model` | `off` / `qwen3:8b` | Local-only by default. |

Do not change a pre-registered threshold, corpus or metric after seeing a result.
**Mobile font binaries stay out of the repository** (owner decision 2026-08-06,
untouched); the consequence is `MOBILE-ASSETS-01` in §5.

## 4. Tests and CI

Full verification of the tree of `77b33d6` — the last work commit, and the only
later change is this closing documentation commit — run through the recorder,
2026-08-08:

```powershell
.venv\Scripts\python.exe scripts\dev_verify.py --full
#   -> git diff --check clean; ruff All checks passed!
#      pytest -q -> 3450 passed, 5 deselected, 362 warnings (563.28s)
#      recorded at 77b33d6e
```

3450 is up from the previous snapshot's 3448 by **+2**: three
`tests/test_output_contract.py` cases were added and one removed (a
characterization test that asserted a known scope limit as `SATISFIED` — the
owner's call not to pin a gap as expected behaviour).

Mobile, 2026-08-08, with the font assets present locally (`MOBILE-ASSETS-01`
means this is not reproducible on a clean clone):

```powershell
cd mobile; flutter analyze   # -> No issues found!
cd mobile; flutter test      # -> 37 passed  (was 17)
```

`tests/test_claude_session_hooks.py` was run on its own for the
`.claude/rules/mobile.md` edit, which it asserts the content of: **192 passed**,
2026-08-08.

**Not run this session, and not claimed as passed:** Electron (`npm test`,
`npm run build`) — untouched. No live workload of any kind: no Ollama run, no
A/B harness, no completion-contract evaluation, no real mailbox. **No live E2E
of the mobile confirmation round-trip against a real server + model** (§5).

**CI, read per job** (`gh run view 31225122022 --json jobs`, 2026-08-08): the
tip that is on origin, `0356120`, is green in **all three** jobs — `python`,
`electron`, `mobile` all `success`. This is **the first CI run that executed
`pytest -n 4 --dist load`**, which was the previous snapshot's own next
priority: the `Test (pytest)` step took **418s**, against 630s for the last
serial run (`31213026002`), with no subprocess timeout. That measurement closes
the open item the parallel switch carried. This session's own commits are not
on origin yet; read their CI live rather than predicting it here.

## 5. Known open issues

Each keeps its identifier; the detail stays in the linked document.

- **Completion-contract Finding 2 — open.** `honest_failure_retried` counts
  eligible rows that carry `repair_attempted`, so a repair that fires on an
  honest failure and then produces *something* leaves the eligible set and is
  never counted. The metric was left exactly as pre-registered — this is a note
  for the next revision of the gate, not a change to this one. Finding 1 is
  **closed** (`a02d4be`); Finding 3 (TTFB) was narrowed by `e200658`, not closed.
  See
  [`docs/eval/completion_contract_pilot_2026-08-05.md`](docs/eval/completion_contract_pilot_2026-08-05.md).
- **Mobile L3 confirmation has never run live.** Verified by `flutter analyze`
  and `flutter test` only — no E2E against a real server + model from the phone.
  This is the same limit `docs/SAFETY.md` records for the Electron HUD's
  confirmation round-trip, and both are now open at once.
- **Mobile confirmation: no cross-tab indicator, and no `conversation_id`.** A
  prompt raised while the user is on another tab is answerable when they return
  (the provider is app-scoped) but nothing signals it from elsewhere in the app.
  `confirmStream()` sends no `conversation_id`, matching `chatStream()`; if
  mobile ever pins a conversation, both must change together or the resume is
  refused server-side.
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
  coarse type, not field CONTENT** — a deliberate scope limit. The verification
  record is checked the same way, plus a pattern on the summary.
- **Source Binding scope limits** (deliberate): a bare-filename request is
  satisfied by a same-named file in a different directory — closing that needs
  disambiguation, and the alternative rule was reverted once because a live
  smoke caught it rejecting the model's own correct answer; plain Unicode
  casefold on Turkish İ/i; pre-execution guard has deterministic evidence only.
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

**Give the mobile L3 round-trip one live run** — a real server, a real model, a
real gated action approved and denied from the phone. It is the only thing
standing between `baf9f8b` and a claim anyone can rely on, and this repository
has been burned specifically here before: a gate once passed 2235 tests and
38/38 mutations, then failed 10/10 live.

After that, **completion-contract Finding 2** is the pilot's remaining open
finding, and its fix belongs to the next revision of the gate rather than to the
current one — do not redefine a pre-registered metric in place. `ROADMAP.md`'s
own next unstarted phase is **Faz 5 (proaktif mail → takvim)**, which is blocked
on the OAuth re-consent in §7.

Do not start any of this — or any product work — inside a session that is
closing.

## 7. Human-required actions

- **Google OAuth re-consent** (Gmail read, Calendar write, Contacts) — blocks the
  Faz 5 live measurement and the Faz 2 entity resolver.
- **Mobile font binaries** — owner deferred 2026-08-06. This is the only blocker
  to running `flutter test` in CI, and it now guards 37 tests rather than 17.
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
- **The identity in `current.json` changed mid-session** (an earlier id was
  recorded at session start, a later one partway through). Nothing was
  hand-authored in response: the helper reads whatever `current.json` holds and
  derives branch and HEAD itself. Worth knowing only because a marker written
  late in a session may not name the id its preflight announced.
- **This session's preflight again warned that the previous session did not
  close, and again it was a false alarm** — the marker on disk read `closed` at
  `0356120`, which was HEAD. Check the marker itself (`claude_session_state.py
  show`) before believing that warning; this is now the second consecutive
  session it has fired wrongly.
- The gitignored `full-verification.json` holds this session's own reusable
  evidence, recorded at `77b33d6`. It is bound to a session id and a commit and
  is **not transferable** — this session correctly found the previous session's
  record `NOT REUSABLE` and re-ran the suite rather than inheriting it.
