---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 1e1116e6ecf141c9aff59d8395d416df1a661a57
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
- The branch tip at the start of this session was **`29f6995`**
  (`fix(workflow): block session close on failed CI`), pushed, and read per job
  as CI run **`31046333431`**: `python` success, `electron` success, `mobile`
  failure — the existing `CI-MOBILE-01` signature.
- This session added three work commits (§2) and this closing HANDOFF commit.
  **Their push state and CI outcome are not asserted here** — both change after
  this file is written. Derive them:

```bash
git rev-list --left-right --count origin/langgraph-migration...HEAD
gh run list --branch langgraph-migration     # then: gh run view <id> --json jobs
```

- Never quote how far `main` is behind. Derive it:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`

## 2. Last completed work

**Session Lifecycle acceptance fixes.** A real CLI acceptance run of Session
Lifecycle v1 confirmed the mechanism works — project hooks are discovered, and
the SessionStart context injection lands in the model's first turn — and found
two structural flaws in it. Both are closed.

1. **Session identity is machine-authored** (`dd41678`). The close marker used to
   be JSON the model typed, so its `session_id` was whatever the model *believed*
   the session was called — inferable only from a transcript filename, from "the
   newest file", or from a guess. Two records agreeing on the same guess looked
   exactly like two records agreeing on the truth. The id now travels one way
   only: SessionStart records the authoritative payload value into the
   gitignored `.claude/session-recovery/current.json`, and
   `scripts/claude_session_state.py` is the only writer of any recovery state.
   `prepare` / `close` / `block` take **no session-id argument at all** and
   refuse on a cross-session marker, a moved HEAD or a changed branch. SessionEnd
   records `identity_status` (`matched` | `current_missing` | `mismatch`); only
   `matched` supports a clean close, and an **absent** field is unverified, not
   clean. CI classification stays with the `/session-close` skill.

2. **HANDOFF freshness is verified from metadata** (`d606987`). Freshness was
   "is the first hex token in the prose an ancestor of HEAD?" — which a stale
   handoff satisfies by construction, since its opening section always names an
   old commit and an old commit is always an ancestor. Freshness is now declared
   in the frontmatter above and **counted**: exactly one commit after
   `covered_through_sha`, and that commit must touch `HANDOFF.md`. Verdicts are
   `current` / `STALE — N commits after covered work` / `INVALID` (including
   `distance == 0`, the self-reference case) / `legacy` (no metadata, refused
   rather than classified). Contract in `.claude/rules/documentation.md`.

3. **`test(calendar): freeze mail fixture clock`** (`1e1116e`). The mail→calendar
   fixture tests built their service with the real wall clock while the fixture
   body said "5 Ağustos" with no year and the assertions hard-coded `2026-08-05`.
   A bare day+month resolves forward, so the file passed until 2026-08-05 and
   then resolved to 2027 permanently. Now frozen at `2026-08-04 12:00`
   Europe/Istanbul via the repository's injectable `jarvis.clock.FrozenClock` —
   no `datetime.now` patching, no product-code change. The expected year was
   deliberately not moved to 2027: the body also says "Çarşamba", which is true
   of 5 August in 2026 and not in 2027.

**Completion Contract Source Binding** (earlier, pushed as `a02d4be`). A
source-bound requirement must trace its artifact back to the source the user
named, through both the producing tool call's arguments and the working-set
object's spec. Non-repairable verdict `OUTPUT_SOURCE_MISMATCH`; in `enforce` a
mismatched `plot_data` call is blocked **before** execution. Shared identity in
`jarvis/execution/source_identity.py`; scope limits in
`docs/eval/completion_contract_source_binding.md`.

## 3. Operational modes and rollout decisions

Defaults as shipped in `jarvis/config.py` (verify there, not here):

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

## 4. Tests and CI

Run on **2026-08-06**, on the tree of `1e1116e` (the last work commit):

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests
#   -> All checks passed!
.venv\Scripts\python.exe -m pytest tests/test_calendar_from_mail.py -q
#   -> 21 passed (9.50s)
.venv\Scripts\python.exe -m pytest tests/test_claude_session_hooks.py -q
#   -> 122 passed (3m27s)
.venv\Scripts\python.exe -m pytest -q
#   -> 3269 passed, 5 deselected (9m34s)
git diff --check
#   -> clean
```

The full-suite figure is the **first** run on this tree: no failures, so nothing
was rerun and nothing is being reported behind a rerun.

Lifecycle flows were additionally smoke-run end to end against **throwaway git
repositories** — never the real `.claude/session-recovery/`: clean close,
CI-blocked close, a mismatched synthetic SessionEnd, and the refusal paths. All
passed, including the guard asserting the owner's real recovery directory was
untouched.

**CI for this session's commits is deliberately not predicted here.** Read it
live per job — a green workflow headline hides failing `continue-on-error` jobs:

```bash
gh run list --branch langgraph-migration
gh run view <id> --json jobs
```

## 5. Known open issues

- **`CI-MOBILE-01` — `mobile` CI fails** (`flutter analyze`, ~71 findings, all
  `info`/`warning`: `deprecated_member_use`, missing declared asset dirs). The
  job is `continue-on-error: true`, so the workflow headline stays green while
  the job is red. Owner decision pending; next engineering priority (§6).
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
  The Flutter app renders **nothing** for confirmations.
- `python_run` is access-controlled, **not sandboxed** (no resource/network limit).
- Proactive turns gate **L3 only**; an unwatched L2 write is mitigated by prompt
  instruction, not structurally closed.
- Four `claude/*` scratch branches (the `.claude/worktrees/*` sessions) hold
  commits unreachable from this branch — verified 2026-08-06 as 5, 1, 7 and 1
  commits. Two may be worth recovering. Do not delete without an explicit
  go-ahead.

## 6. Next engineering priority

**`CI-MOBILE-01`.** It is the only permanently-red job on the branch, and
`continue-on-error` currently hides it behind a green headline — the same "an
unfinished check reads as passed" shape the reporting standard exists to
prevent. Either clear the ~71 `flutter analyze` findings, or make the
suppression an explicit reviewed decision rather than an accident of
configuration.

**After that: completion-contract streaming / TTFB architecture.** The latency
clause is the one pre-registered gate clause still failing (§5). Either make a
contracted turn emit before the graph finishes, or accept the TTFB cost and
revise the ceiling *for a future gate* — never retroactively for the pilot
already run.

Do not start either — or any product work — inside a session that is closing.

## 7. Human-required actions

- **Google OAuth re-consent** (Gmail read, Calendar write, Contacts) — blocks the
  Faz 5 live measurement and the Faz 2 entity resolver. The mail→Excel→chart
  chain is 10/10 on fixture data and has still never run against the real
  mailbox; the Gmail live test cannot start until this is done.
- **`CI-MOBILE-01` decision**: clear the ~71 analyzer findings, or keep
  `continue-on-error` and accept that the job stays red.

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
- **Migration note (2026-08-06):** the session that built the machine-authored
  identity chain started *before* `current.json` existed, so it deliberately
  produced no marker for itself and invented no session id. The first fully
  machine-authored lifecycle begins with the next session opened after this work
  is pushed — its preflight will legitimately report the previous session as
  unclosed.
