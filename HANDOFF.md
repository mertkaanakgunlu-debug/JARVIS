---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: a6cb10efb4cb91b61c6215214c0212abb5ab9f3e
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
- The branch tip at the start of this session was **`08b15e0`**
  (`docs: refresh handoff after clearing CI-MOBILE-01`), already pushed. The
  **previous** session pushed it, then blocked: CI run **`31117623901`** never
  reached a verdict — `python` was cancelled mid-run (`Test (pytest)` logged
  `2704 passed, 5 deselected` then `KeyboardInterrupt` / `##[error]The
  operation was canceled.`) and `electron` died in `Set up job`
  (`Failed to resolve action download info. Error: Service Unavailable`) — a
  GitHub Actions provider outage, not a code failure. That session recorded
  `.claude/session-recovery/close-marker.json` as `state: blocked, reason_code:
  CI_INFRA_UNAVAILABLE, run_id: 31117623901, blocking_jobs: [python, electron]`
  under its own session id and stopped, per protocol.
- This session added one work commit (§2), `a6cb10e`, on top of `08b15e0`. It
  is **NOT pushed** — derive live, never trust a stored number:

```bash
git rev-list --left-right --count origin/langgraph-migration...HEAD
gh run list --branch langgraph-migration     # then: gh run view <id> --json jobs
```

- **The inherited `blocked` marker from `08b15e0` is untouched by this
  session and was never relabelled `closed`.** It is history now, not a
  verdict on this session's own work — see §8. This session's own commit has
  no CI evidence yet, because nothing from this session has been pushed.
- On top of `a6cb10e` sits this closing HANDOFF commit. **Its push state and CI
  outcome are not asserted here** — both change after this file is written.
  Derive them with the same two commands above, against `HEAD` at the time of
  asking.
- Never quote how far `main` is behind. Derive it:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`

## 2. Last completed work

**CI/session-lifecycle recovery hardened — three GPT-lead review rounds,
2026-08-07, one commit (`a6cb10e`, `fix(session): harden CI recovery
lifecycle`).** Follow-on to the `CI_INFRA_UNAVAILABLE` block the previous
session recorded on `08b15e0`: that block is a real, permanent capability gap
(a pushed tip can lose its only CI verdict to a provider outage, with no way to
re-judge the exact same tip), and this session closed the gap in the recovery
*protocol* without touching CI infrastructure, `main`, or any product code.

**Round 1 — the recovery mechanism and the reason-code vocabulary.**

- The prior session's own working tree had added a `workflow_dispatch` trigger
  to `.github/workflows/ci.yml` as a manual re-run mechanism. Verified against
  the live repository and **removed**: GitHub resolves `workflow_dispatch` from
  the repository's **default branch**, which is `main`
  (`gh repo view --json defaultBranchRef` → `main`), and `main` carries **no**
  `.github/` directory at all (`git ls-tree -r --name-only origin/main --
  .github` → empty). A trigger that only exists on `langgraph-migration` is
  unreachable — worse than no mechanism, because it reads as a recovery path
  while being a dead end. `.github/workflows/ci.yml` is now byte-identical to
  its state before that session's edit.
- `scripts/claude_session_state.py`'s `block --reason-code` used to be a shape
  check (`^[A-Z][A-Z0-9_]{0,63}$`) while its own comment claimed a fixed
  vocabulary — the two disagreed, so any UPPER_SNAKE string was accepted.
  `KNOWN_REASON_CODES` is now a real closed set, enforced on write only:
  `CI_BLOCKING_FAILURE` (a blocking job actually failed — fix the code) and
  `CI_INFRA_UNAVAILABLE` (the provider never reached a verdict — nothing about
  the tree is known, in either direction; terminal for the session that hits
  it). Reading stays open — a marker written before the vocabulary closed, or
  naming a future code, still renders.
- The SessionStart preflight used to render both reason codes identically
  (verified false against the pre-fix code: two markers differing only in
  `reason_code` produced byte-identical context blocks). It now leads with the
  code and a code-specific instruction — `CI_BLOCKING_FAILURE` says fix it
  before closing anything on top; `CI_INFRA_UNAVAILABLE` says the tip is
  unproven rather than failing, and does not by itself block this session's own
  work.
- `scripts/claude_session_state.py prepare` used to allow `blocked → prepared →
  closed` for the **same** session that earned the block — one extra step past
  the already-forbidden `blocked → closed`, reaching the identical place.
  Measured against the pre-fix helper: `prepare` returned 0 and walked a
  `blocked` marker back to `prepared` with no new CI evidence of any kind.
  `prepare` now refuses outright when the CURRENT session already holds its own
  `blocked` marker, scoped by identity so a **later** session inheriting the
  same marker can still `prepare` — that inherited route is the only honest way
  out of a block.
- `.claude/skills/session-close/SKILL.md` rewritten: `CI_INFRA_UNAVAILABLE` is
  now documented as **terminal** for the session (one rerun, then blocked, then
  stop — no empty commit, no repeated rerun, no `workflow_dispatch`, no
  touching `main`), and the old "known cosmetic mobile signature" wave-through
  language (already retired the prior session) does not return.

**Round 2 — an existing-but-unreadable marker must fail closed.** `prepare`'s
new identity guard read `_read_json(marker)` and treated `None` — which
`_read_json` returns for both "no file" and "file exists but will not parse" —
as "nothing to lose." Measured against the pre-fix worktree: six corruption
shapes (truncated JSON, non-JSON text, an empty file, a JSON list/string/null)
all returned `prepare` exit 0 and **silently overwrote** the marker, including
a truncated one whose surviving bytes still read `"state": "blocked"`. Fixed
with `_read_marker_or_refuse`, which keeps "absent" apart from "exists but
cannot be trusted" and raises rather than returns for the latter — `prepare`
never repairs, renames, regenerates, or guesses at a marker it cannot read; it
stops and reports. `close`/`block` route through the same reader now too, so
their error message stopped claiming `no close marker exists` for a file that
plainly exists.

**Round 3 — parseable JSON is not automatically a genuine marker.** Round 2's
fix checked parse success and dict-type, which `{}` and an incomplete
`{"state": "blocked"}` (missing `session_id`) both pass — and passing let them
straight through: an empty object has no `state`, so the identity guard read
"not blocked"; an incomplete `blocked` entry with no `session_id` read as
"blocked, but not this session's," which is exactly the shape of a legitimate
inherited marker. Measured against the pre-round-3 worktree: five such objects
all returned `prepare` exit 0 and got overwritten. `_marker_structural_defect`
now checks the full shape each write path actually produces — schema version,
a recognised state, a usable `session_id`, a full 40-character `head` SHA, a
non-empty `branch`, and that state's own required fields (`reason_code`'s
*presence*, never revalidated against the vocabulary, so historical codes stay
readable) — before a marker is trusted enough to overwrite.

All three rounds: no product code touched (`jarvis/` untouched), no new
dependency, `.github/workflows/ci.yml` unchanged from `08b15e0`. Falsifiability
was measured directly against the pre-fix code for every round, not asserted —
see `a6cb10e`'s test additions (167 new/changed cases across the three rounds
in `tests/test_claude_session_hooks.py`, lifecycle file: **122 → 179**).

## 3. Operational modes and rollout decisions

Defaults re-read from `jarvis/config.py` on 2026-08-07 (verify there, not
here) — **unchanged this session**:

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
both failed. Nothing in this session touched it. Do not change a pre-registered
threshold, corpus or metric after seeing a result.

**Mobile font binaries stay out of the repository** (owner decision,
2026-08-06, untouched this session). `mobile/.gitignore` keeps ignoring
`assets/fonts/*.ttf`; the consequence is recorded as an open issue in §5.

## 4. Tests and CI

Run on **2026-08-07**, on the tree of `a6cb10e` (the last work commit,
committed but not pushed):

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests
#   -> All checks passed!
.venv\Scripts\python.exe -m pytest -q
#   -> 3326 passed, 5 deselected, 362 warnings (560.64s)
git diff --check
#   -> clean
```

The full-suite figure is the **first** run on this exact tree: no failures, so
nothing was rerun and nothing is being reported behind a rerun. The lifecycle
file specifically (`tests/test_claude_session_hooks.py`) accounts for **179**
of those, run standalone as well: `179 passed in 275.69s`.

**No CI run exists yet for `a6cb10e`** — it has not been pushed, so `push`
never fired for it. This is stated as an honest gap, not an unrun check
reported as passed:

```bash
gh run list --branch langgraph-migration
#   -> newest run is still 31117623901, against 08b15e0
```

The last CI evidence that exists is still the previous session's, for
`08b15e0`, run `31117623901` — **incomplete**, see §1 and §8 for its exact
per-job signature. Read this closing commit's own CI live, once it is pushed:

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
  untouched (again, this session); pinning is an open option, not a decision.
- **`CI-FLAKE-CHROMA-01` — transient suspected, root cause unproven.** Runs have
  shown first-run failures with `chromadb ... no such table: acquire_write`
  across files a commit never touched; `chromadb>=0.6` is unpinned in
  `requirements.txt` (1.5.9 installed locally). Rerun a failed job **once** only
  when the failure is not explainable by the diff, never claim the rerun proved
  a root cause, and never classify a failure as this flake without reading its
  actual signature. Distinct from `CI_INFRA_UNAVAILABLE` (§8): this is a
  suspected code/dependency-timing issue with a specific log signature, the
  other is the provider never running the job at all.
- **A lost CI verdict (`CI_INFRA_UNAVAILABLE`) has no recovery mechanism
  beyond "the next push judges the next tip."** This is now a documented,
  deliberate limit rather than an oversight (§8) — `workflow_dispatch` was
  tried and removed this session because it cannot work on this repository's
  default-branch layout (§2). If a pushed tip needs to be re-judged on its
  *exact* SHA without a new commit, that still has no mechanism; the owner
  would need to either fix the default-branch/`.github/` layout or accept the
  gap.
- **`scripts/claude_session_state.py`'s marker structural check does not
  validate field CONTENT, only presence and coarse type** (e.g. `prepared_at`
  just needs to be a non-empty string, not a valid timestamp; `blocking_jobs`
  just needs to be a list). Deliberate scope limit from this session's round 3
  — depth belongs to the write-time validators, this only decides whether an
  object is safe to read as a marker at all. If a future state is added to the
  three the module writes (`prepared`/`closed`/`blocked`), `_STATE_REQUIRED_FIELDS`
  and `_LIFECYCLE_STATES` need updating alongside it, or markers of the new
  state will be structurally rejected.
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
  commits unreachable from this branch — last re-derived 2026-08-06 as 5, 1, 7
  and 1 commits (`eager-noether-46af01`, `gifted-wilbur-e021ea`,
  `stoic-spence-2c5246`, `thirsty-mclean-f67665`). Two may be worth recovering.
  Do not delete without an explicit go-ahead.

## 6. Next engineering priority

**Completion-contract streaming / TTFB architecture.** Unchanged by this
session, which was entirely session-lifecycle/CI-recovery protocol work: the
latency clause is the one pre-registered gate clause still failing (§5):
treatment first-visible p90 ≈ 99.8 s against a 60 s ceiling. Either make a
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
- **Default-branch / `.github/` layout** (surfaced this session, §5): the repo's
  default branch (`main`) carries no `.github/` directory, which is why
  `workflow_dispatch` cannot work as a manual CI-recovery mechanism on
  `langgraph-migration`. Not acted on — flagged as a fact for the owner to
  decide whether it is worth changing, not a decision made on their behalf.

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
- **`CI_INFRA_UNAVAILABLE` is a terminal, non-code-failure blocked state, and
  it is now enforced by the state machine, not just documented.** As of this
  session: `prepare` refuses outright for the SAME session that holds its own
  `blocked` marker (no `blocked → prepared → closed` detour), but a LATER
  session inheriting that marker under a different identity may `prepare`
  normally — the marker is history, not a verdict on the new session's work.
  `close`/`block` refuse just as before on a marker they cannot trust, and now
  say so accurately (`close marker exists but ...`) instead of claiming one
  does not exist. See §2 for the three-round fix and `.claude/skills/
  session-close/SKILL.md` §4/§5 for the enforced policy text.
- **This session's own marker is exactly what it should be at this point in
  the protocol: `prepared`, under the CURRENT authoritative session id,
  written by this step (§ below) — not written by hand.** The inherited
  `blocked` marker from the previous session (identity `18b03d4e...`, reason
  `CI_INFRA_UNAVAILABLE`, run `31117623901`) was read, reported, and left
  exactly as it was; this session's identity was confirmed different from it
  before any work began, per the session-boundary check the SessionStart
  preflight and `claude_session_state.py show` both support.
- A next session whose preflight says the previous one did **not** close, or
  whose SessionEnd identity was **UNVERIFIED**, should reconcile before starting
  new work.
