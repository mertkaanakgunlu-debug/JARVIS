---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: d8ab406bfccf67e6b0323ed2904012e51d2d9ea1
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
  untouched. Derive changing branch relationships live.
- Work commit `d8ab406` closes Completion Contract Finding 2 with an additive
  call-level diagnostic while preserving the historical pre-registered gate
  and committed results.
- The corrected diagnostic uses completion-repair boundaries and the always-on
  execution ledger's ordered `tool_call_id` evidence. Final success can no
  longer erase an earlier honest-failure retry from this diagnostic.
- The 4224-second run is disposed as a historical observability anomaly. Its
  exact phase cannot be reconstructed from the old row; later bounded phase
  instrumentation remains available if it recurs.
- Push authority remains owner-only. Derive remote state live before any future
  finalize action.

## 2. Last completed work

Commit `d8ab406` (`fix(evals): diagnose retried honest tool failures`) completed
the Finding 2 correction:

- `scripts/completion_contract_ab.py` now records the call-ID set at each
  completion-repair boundary and emits a separate
  `honest_failure_retried_diagnostic` with redacted failed/retry call IDs.
- `jarvis/evals/contract_metrics.py` correlates an actual failed execution
  before repair with a distinct later execution of the same capability. It
  excludes invalid arguments, pre-execution blocks, user denials,
  `MISSING_NO_ATTEMPT`, and `outcome=unknown`.
- Regression coverage deterministically reproduces failure → repair → later
  success, proves the historical final-state predicate misses it, and pins
  same-name calls by `tool_call_id`.
- The disposition and preserved evidence are recorded in
  `docs/eval/completion_contract_finding2_disposition_2026-08-10.md`. No large
  or live A/B was run.

## 3. Operational modes and rollout decisions

- The pre-registered completion-contract gate, its thresholds, corpus,
  historical trial rows, and committed summaries are unchanged. The new metric
  is diagnostic and reporting-only; `_gate_verdict()` still computes the
  original clause.
- `required_outputs_mode` and all rollout defaults are unchanged.
- Approve-side Result Binding remains always-on. In
  `execution_contract_mode="off"` and `"shadow"`, envelopes remain
  observation-only; only `enforce_*` modes may make verified postconditions
  authoritative. Unknown outcomes stay unknown in every mode.
- No real Gmail, Calendar, or Drive write was used for this work.

## 4. Tests and CI

Deterministic evidence collected on 2026-08-10:

- Focused command
  `.venv\Scripts\python.exe -m pytest tests/test_completion_contract_harness.py tests/test_completion_contract_ttfb_metrics.py -q`
  passed `56` tests with `1` warning in `3.84s` after the final focused edit.
- The first selector invocation was terminated by its command wrapper after
  about 124 seconds and produced no test verdict. The completed command
  `.venv\Scripts\python.exe scripts\dev_verify.py --base 73e0dbf3024c7f5a2e3514681eab833fd5f4d2bb --run`
  selected `FULL PYTHON FALLBACK`; diff check and Ruff passed, and pytest
  reported `3522 passed, 5 deselected, 410 warnings in 709.36s`.
- After the work commit,
  `.venv\Scripts\python.exe scripts\dev_verify.py --full` passed and recorded
  exact-tree evidence for `d8ab406`: diff check and Ruff passed; pytest reported
  `3522 passed, 5 deselected, 410 warnings in 725.13s`.
  `.venv\Scripts\python.exe scripts\claude_session_state.py verification`
  reported the record `REUSABLE`.
- Electron and mobile files were untouched, so their suites were not run. No
  live model workload was run.

## 5. Known open issues

- Mobile confirmation still lacks a cross-tab indicator and does not carry
  `conversation_id` through the confirmation path.
- `MOBILE-ASSETS-01`: a clean clone lacks the gitignored font binaries required
  for a full mobile build/test. The wake-word ONNX model is also absent.
- `chromadb` remains unpinned. Flutter defaults/version pinning and full
  selector coverage remain incomplete.
- Source-binding coverage and mail/calendar live evidence remain deliberately
  scoped; the real mailbox path was not exercised here.
- `python_run` is confirmation-gated but not sandboxed. Proactive L2 behavior
  still relies on prompt-level mitigation.
- `.Codex/worktrees/*` contains historical scratch worktrees with commits not
  all reachable from `langgraph-migration`; do not remove them without owner
  approval.

## 6. Next engineering priority

Address mobile confirmation continuity: carry `conversation_id` through the
confirmation path and add the cross-tab indicator. Keep approval provenance,
multi-confirmation, and exactly-once resume behavior intact.

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
- The reusable full-verification record belongs to work commit `d8ab406`, branch
  `langgraph-migration`, and the current lifecycle identity. Verify it with the
  helper before attempting to reuse it.
- A correctly prepared close has exactly one documentation commit after the
  covered work SHA. Derive marker and repository state live rather than trusting
  remembered remote or CI state.
