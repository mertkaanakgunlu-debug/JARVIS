# HANDOFF — current state

Single-state snapshot, rewritten at each session close. Not a history: `git log`
and `CHANGELOG.md` own that. **If anything here contradicts the repository, the
repository is right and this file is the bug** — re-derive rather than trust.

Imported automatically by `CLAUDE.md`, so it is read every session.

## 1. Current verified state

- Branch: **`langgraph-migration`** (the active branch; `main` is a strict
  ancestor and behind).
- Last commit verified green by CI at **job level**: **`a02d4be`**
  (`fix(contract): bind chart completion to requested source`).
- Local additionally carries **one work commit (`7816253`, Session Lifecycle v1)
  and this closing HANDOFF commit**, both **unpushed** at the time of writing.
  Their CI outcome is therefore unknown here — read it live.
- Never quote how far `main` is behind. Derive it:
  `git rev-list --left-right --count origin/main...origin/langgraph-migration`
- Read branch CI live: `gh run list --branch langgraph-migration`, then
  `gh run view <id> --json jobs`.

## 2. Last completed work

**Completion Contract Source Binding** (pushed, `a02d4be`). The contract's
success test used to mean only "a chart artifact was declared and the working
set kept it", so a completion repair could satisfy a request for file X by
drawing file Y and recording `SATISFIED` — the pilot's finding 1. Now a
source-bound requirement must trace its artifact back to the source the user
named, through **both** the producing tool call's arguments and the working-set
object's spec, or the verdict is not success. New non-repairable verdict
`OUTPUT_SOURCE_MISMATCH`; in `enforce` a mismatched `plot_data` call is blocked
**before** execution (`blocked_output_source_mismatch`) rather than detected
after. Shared identity lives in `jarvis/execution/source_identity.py`.
Details and scope limits: `docs/eval/completion_contract_source_binding.md`.

**Session Lifecycle v1** (this session, local). SessionStart preflight hook,
SessionEnd recovery hook, `/session-close` skill, `.claude/rules/*` path-scoped
rules, and a `CLAUDE.md` reduced to a permanent operating contract that imports
this file. `.claude/settings.local.json` is now **untracked and gitignored** —
it is per-machine state (permission allowlist, absolute paths) and while tracked
it left the working tree permanently dirty; the local file stays on disk and
keeps working. Shared project config lives in `.claude/settings.json`, which
carries hooks only — no permissions block, nothing pre-authorising push. No
product behaviour code touched.

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
both failed. Source Binding fixes a correctness finding; it does **not** re-run
or re-open that gate. Do not change a pre-registered threshold, corpus or metric
after seeing a result.

## 4. Tests and CI

Run on **2026-08-05**, on the tree that became this session's work commit:

```powershell
.venv\Scripts\python.exe -m ruff check jarvis scripts tests   # All checks passed!
.venv\Scripts\python.exe -m pytest -q                          # 3193 passed, 5 deselected (7m08s)
git diff --check                                               # clean
```

CI for `a02d4be` (run `31021655125`), read per job:

| job | result |
|---|---|
| `python` | **failed first run**, passed on a single rerun (3147 passed, 5 deselected) |
| `electron` | success (not rerun) |
| `mobile` | **failed** — pre-existing, `continue-on-error: true` |

The overall workflow reads `success`; that is **not** the same as all jobs
passing. Always check jobs.

All 21 first-run `python` failures were the identical
`chromadb ... no such table: acquire_write`, spread across files the commit diff
never touched. Recorded as **`CI-FLAKE-CHROMA-01 — transient suspected, root
cause unproven`**; `chromadb>=0.6` is unpinned in `requirements.txt`. Rerun a
failed job **once** only when the failure is not explainable by the diff, and
never claim the rerun proved a root cause.

## 5. Known open issues

- **`mobile` CI fails** (`flutter analyze`, ~71 findings, all `info`/`warning`:
  `deprecated_member_use`, missing declared asset dirs). Owner decision pending.
- **`CI-FLAKE-CHROMA-01`** as above — unpinned `chromadb`, root cause unproven.
- **Source Binding scope limits** (documented, deliberate): a *bare*-filename
  request cannot disambiguate two same-named files in different directories;
  plain Unicode casefold does not equate Turkish `İ`/`i` across case; the
  pre-execution guard has **deterministic evidence only** — no live run has yet
  made the model attempt a wrong source, so it has never fired live.
- **Completion-contract latency**: treatment first-visible p90 ≈ 99.8 s against a
  60 s ceiling. Untouched by Source Binding.
- Faz 5 (mail → calendar) is green on fixtures but **has never run against the
  real mailbox**; background ingestion stays off until it does.
- Electron HUD confirmation is compile/parser-verified only — **no live E2E**.
  The Flutter app renders **nothing** for confirmations.
- `python_run` is access-controlled, **not sandboxed** (no resource/network limit).
- Proactive turns gate **L3 only**; an unwatched L2 write is mitigated by prompt
  instruction, not structurally closed.
- Four `.claude/worktrees/*` branches hold commits unreachable from this branch;
  two may be worth recovering. Do not delete without an explicit go-ahead.

## 6. Next engineering priority

Decide the **completion-contract latency** question, since it is the one failing
gate clause Source Binding did not address: either make a contracted turn emit
before the graph finishes, or accept the TTFB cost and revise the pre-registered
ceiling *for a future gate* (never retroactively for the pilot already run).

Do not start this — or any product work — inside a session that is closing.

## 7. Human-required actions

- **Push approval** for this session's commits (Session Lifecycle v1). Nothing
  is pushed without an explicit in-chat go-ahead; `/session-close finalize`
  refuses to run without one.
- **Google OAuth re-consent** (Gmail read, Calendar write, Contacts) — blocks the
  Faz 5 live measurement and the Faz 2 entity resolver.
- **`mobile` CI decision**: clear the ~71 analyzer findings, or keep
  `continue-on-error` and accept the job stays red.

## 8. Session recovery notes

- A **SessionStart** hook (`scripts/claude_session_start.py`) injects the
  repository preflight, so no orientation prompt needs pasting. It is fail-open:
  if it reports `SESSION PREFLIGHT DEGRADED`, re-derive state manually.
- A **SessionEnd** hook writes `.claude/session-recovery/latest.json` (local,
  gitignored) on every exit. It never commits, pushes, or edits a tracked file.
- `/session-close` owns closing: `prepare` verifies and commits without pushing;
  `finalize` pushes only on explicit approval and marks the recovery state
  `closed`. A next session whose preflight says the previous one did **not** run
  `/session-close` should reconcile before starting new work.
