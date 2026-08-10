---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 07a58bea113e3bc1ed42208fda56c450ef44e793
---

# HANDOFF — current state

Single-state snapshot, rewritten at each session close. It is not history:
`git log` and `CHANGELOG.md` own that. If this document conflicts with the
repository, the repository is right and this file is the bug. The
`covered_through_sha` is the last work commit described here, never the closing
documentation commit.

## 1. Current verified state

- The active branch is `langgraph-migration`. At preparation time on
  2026-08-10, local and remote `main` both resolved to `5f6f6ff` and remained
  untouched. Derive all changing branch relationships live.
- Work commit `07a58be` code-enforces approve-side terminal result binding for
  genuinely user-approved external writes across chat, streaming, confirmation
  resume, background, and voice-visible completion paths.
- The confirmation gate, signed execution request, exactly-once accounting,
  and always-on execution ledger remain the authority for approval provenance
  and actual tool/API success, failure, or unknown outcome.
- Push authority remains owner-only. Derive branch state with `git fetch origin`
  and `git rev-list --left-right --count origin/langgraph-migration...HEAD`.

## 2. Last completed work

Commit `07a58be` (`fix(agent): bind approved action results to runtime truth`)
closed the approve-side Result Binding correctness gap:

- Post-approval model prose is buffered until terminal state, then the shared
  finalizer replaces it with a bounded, code-authored receipt derived from safe
  execution facts. Raw arguments, results, paths, identifiers, and secrets are
  not rendered.
- Binding is limited to external writes whose exact signed request was approved
  by the user. Auto-approved, read-only, local, denied, and legacy calls do not
  gain approval provenance. Multi-confirmation and exactly-once behavior are
  preserved.
- Timeouts remain `outcome=unknown` and are never retried or upgraded. Duplicate
  execution identifiers cannot cross-bind verification evidence.
- Deterministic fake-write graph, finalizer, SSE, multi-confirmation, and voice
  coverage verifies the path without performing a real Gmail, Calendar, or
  Drive write. A post-fix live external-write E2E was deliberately not run.

## 3. Operational modes and rollout decisions

- Approve-side ledger binding is always-on and independent of
  `execution_contract_mode` and `required_outputs_mode`.
- In `execution_contract_mode="off"` and `"shadow"`, execution envelopes and
  postcondition verification are observation-only. User-visible receipts use
  only approval provenance, external-write classification, ledger `ok`, and
  `outcome=unknown`.
- Only a mode beginning with `enforce_` may treat a confirmed postcondition as
  independently verified success or a verification failure as user-visible
  failure. The common finalizer receives the mode explicitly; result binding
  does not read global settings implicitly.
- Unknown outcomes remain unknown in every rollout mode. No rollout default was
  widened.

## 4. Tests and CI

Deterministic evidence collected on 2026-08-10:

- Focused command
  `.venv\Scripts\python.exe -m pytest -q tests/test_approve_result_binding.py tests/test_confirmation_resume_trace.py tests/test_output_contract_streaming.py tests/test_prepare_execution_node.py tests/test_streaming_interrupt_fallback.py tests/test_voice_progress_acknowledgement.py`
  passed `127` tests with `25` warnings in `24.59s`.
- The first invocation of
  `.venv\Scripts\python.exe scripts\dev_verify.py --base 6476711e17846a7f441ded60714c6a8dca8a4200 --run`
  was terminated by the command wrapper after `124.1s` with exit `124` and no
  test verdict. The rerun with sufficient command time passed the selector's
  `101` selected files: `2295 passed, 5 deselected, 277 warnings in 561.87s`;
  selector Ruff and `git diff --check` also passed. The selector did not request
  `FULL PYTHON FALLBACK`.
- After the work commit, `.venv\Scripts\python.exe scripts\dev_verify.py --full`
  passed and recorded exact-tree evidence for `07a58be`: Ruff and diff check
  passed; pytest reported `3514 passed, 5 deselected, 410 warnings in 726.05s`.
  `.venv\Scripts\python.exe scripts\claude_session_state.py verification`
  reported that record `REUSABLE`.
- Electron and mobile files were not changed, so their suites were not rerun.
  No live model workload or real Gmail, Calendar, or Drive write was run. CI was
  not inspected because this session did not push.

## 5. Known open issues

- Completion-contract Finding 2 remains open: the instrumented 4224-second
  anomaly has not been explained, even though it did not recur in the later
  pooled live trials documented in
  `docs/eval/completion_contract_ttfb_followup_2026-08-07.md`.
- Mobile confirmation still lacks a cross-tab indicator and does not carry
  `conversation_id` through the confirmation path.
- `MOBILE-ASSETS-01`: a clean clone lacks the gitignored font binaries required
  for a full mobile build/test. The wake-word ONNX model is also absent.
- `chromadb` remains unpinned. Flutter defaults/version pinning and full
  selector coverage remain incomplete.
- Source-binding coverage and mail/calendar live evidence remain deliberately
  scoped; the real mailbox path was not exercised by Result Binding work.
- `python_run` is confirmation-gated but not sandboxed. Proactive L2 behavior
  still relies on prompt-level mitigation.
- `.Codex/worktrees/*` contains historical scratch worktrees with commits not
  all reachable from `langgraph-migration`; do not remove them without owner
  approval.

## 6. Next engineering priority

Investigate completion-contract Finding 2 and the unexplained 4224-second
instrumented anomaly. Reconcile the harness timing/termination path with the
later non-recurrence before changing rollout defaults or treating the anomaly
as resolved. Do not begin that investigation as session-close work.

After that, address mobile confirmation continuity (`conversation_id` and the
cross-tab indicator).

## 7. Human-required actions

- Complete Google OAuth re-consent when real Google integration testing resumes.
- Supply or license the mobile font binaries needed for clean-clone builds.
- Decide which Flutter version is canonical and whether Flutter tests join the
  default selector loop.
- Decide the long-term default-branch and `.github` layout.
- Preserve machine-specific Android SDK setup as local-only configuration.

## 8. Session recovery notes

- Session identity is machine-authored. Never infer an ID or hand-write
  `current.json`, `close-marker.json`, or recovery JSON; use
  `scripts/claude_session_state.py` for lifecycle transitions.
- The reusable full-verification record belongs to work commit `07a58be`, branch
  `langgraph-migration`, and the current lifecycle identity. Verify it with the
  helper before attempting to reuse it.
- A correctly prepared close has exactly one documentation commit after the
  covered work SHA. Derive marker state and repository state live rather than
  trusting remembered push or CI status.
