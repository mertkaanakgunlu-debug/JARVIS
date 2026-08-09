---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 6bbf0b41f5e55756bb6598a9b4e2bf0cae4d569a
---

# HANDOFF — current state

Single-state snapshot, rewritten at each session close. It is not history:
`git log` and `CHANGELOG.md` own that. If this document conflicts with the
repository, the repository is right and this file is the bug. The
`covered_through_sha` is the last work commit described here, never the closing
documentation commit.

## 1. Current verified state

- The active branch is `langgraph-migration`. At preparation time, local and
  remote `main` both resolved to `5f6f6ff`, and both were strict ancestors of
  the work HEAD. Derive all changing ahead/behind information live.
- Work commit `6bbf0b4` unifies Claude Code and Codex repository infrastructure
  without changing product runtime behavior.
- `AGENT_CONTRACT.md` is the canonical permanent repository contract.
  `CLAUDE.md` and `AGENTS.md` are thin client adapters; the repository, not
  either adapter, is the final source of truth.
- `.claude/rules/*.md` and `.claude/skills/session-close/SKILL.md` are the
  canonical shared rules and close workflow. `.Codex/rules/*.md` and
  `.agents/skills/session-close/SKILL.md` are compatibility adapters only.
- Claude and Codex share `.claude/session-recovery/`. Only one root lifecycle
  session may operate in a checkout at a time; parallel work requires separate
  worktrees.
- Push authority remains owner-only. Derive branch state with
  `git fetch origin` and
  `git rev-list --left-right --count origin/langgraph-migration...HEAD`.

## 2. Last completed work

Commit `6bbf0b4` (`chore(agent): unify Claude and Codex infrastructure`) made
the repository's agent operating model client-neutral:

- Added `AGENT_CONTRACT.md`, reduced `CLAUDE.md` and `AGENTS.md` to adapters,
  and preserved Claude's `@HANDOFF.md`, rule auto-loading, and skill discovery.
- Made `.claude/skills/session-close/SKILL.md` the complete canonical close
  procedure and the `.agents` skill a thin Codex adapter.
- Added `.codex/hooks.json`; its SessionStart and SessionEnd command strings are
  byte-for-byte identical to `.claude/settings.json` and resolve the repository
  root before launching the venv interpreter.
- Updated the shared rule/document references, corrected Electron/mobile
  safety-status wording, and added `tests/test_agent_interop.py` plus selector
  coverage in `scripts/dev_verify.py`.
- `.gitignore` already covered the shared lifecycle artifacts and did not need
  a change.

The final command-path smoke was lifecycle-neutral: from the repository root
and from `scripts/`, the hook-shaped PowerShell command resolved
`C:/Users/mertk/Desktop/Jarvis`, changed to the repository root, and launched
`.venv/Scripts/python.exe --version` successfully (`Python 3.14.6`). No
lifecycle script was invoked during the smoke.

## 3. Operational modes and rollout decisions

- Product runtime modes and rollout defaults are unchanged by the interop
  commit.
- The permanent contract, rules, close procedure, lifecycle hooks, marker
  directory, and verification record are shared across Claude and Codex.
- Historical Claude-prefixed Python module and script names remain in place for
  compatibility; names do not imply Claude-only ownership.
- A second root agent session in the same checkout is unsupported because the
  clients intentionally share lifecycle identity and close-marker state.

## 4. Tests and CI

Deterministic evidence for exact work commit `6bbf0b4`:

- `.venv\Scripts\python.exe scripts\dev_verify.py --full` passed on
  2026-08-09. Diff check and Ruff passed; pytest reported
  `3479 passed, 5 deselected, 402 warnings in 565.95s (0:09:25)`.
- `.venv\Scripts\python.exe scripts\claude_session_state.py verification`
  reported `REUSABLE` for `6bbf0b4` with the same counts.
- The interop-focused verification passed `13` tests with `1` warning. The
  task selector later passed `267` tests with `1` warning in `233.72s`, with
  Ruff and diff check clean.
- On 2026-08-10, root and `scripts/` hook-command smokes both resolved and
  entered the repository root and launched the venv interpreter. Parsed hook
  configuration confirmed exact SessionStart/SessionEnd parity.
- The closing-doc selector at base `6bbf0b4` recognized only `HANDOFF.md`,
  reused the exact full record, passed `git diff --check`, and passed
  `tests/test_handoff_contract.py` with `6 passed, 1 warning`.

The interop task did not change product code, so Electron, mobile, and external
live suites were not rerun for it. CI status is live evidence and must be
derived per job from the current remote run; a workflow-level green result is
not sufficient when jobs can be `continue-on-error`.

## 5. Known open issues

- Electron approve-turn transport and exactly-once execution are covered, but
  the assistant's final answer can still express uncertainty after a clean tool
  result. Result-to-completion binding remains the top product gap.
- Mobile confirmation lacks a cross-tab indicator and does not carry
  `conversation_id` through the confirmation path.
- Completion-contract Finding 2 remains open; the instrumented
  `completion_contract_ab` 4224-second anomaly is not yet explained.
- `MOBILE-ASSETS-01`: a clean clone lacks the gitignored font binaries required
  for a full mobile build/test. The wake-word ONNX model is also absent from the
  checkout.
- The Electron branch of `scripts/dev_verify.py` has not been exercised live in
  every supported environment, and Flutter defaults/version pinning remain
  incomplete.
- `chromadb` remains unpinned.
- There is no CI infrastructure recovery beyond a later run when infrastructure
  itself is unavailable; CI must still be inspected per job.
- The lifecycle marker validates coarse structure, not semantic truth.
- Source-binding coverage and mail/calendar live evidence remain deliberately
  scoped; the real mailbox path has not been exercised.
- `python_run` is confirmation-gated but not sandboxed. Proactive L2 behavior
  still relies on prompt-level mitigation.
- `.Codex/worktrees/*` contains historical scratch worktrees with commits not
  all reachable from `langgraph-migration`; do not remove them without owner
  approval.

## 6. Next engineering priority

The next product task is Electron approve-side completion/result binding:

1. Reproduce the misleading final-answer behavior after a successful approved
   tool execution.
2. Define a code-enforced completion contract that binds the final answer to
   the actual tool result without weakening confirmation or exactly-once
   guarantees.
3. Add focused deterministic coverage, then run the selector-derived suite and
   the relevant Electron live path.

After that, return to completion-contract Finding 2 and the unexplained timing
anomaly. Do not begin either item as part of session-close work.

## 7. Human-required actions

- Complete Google OAuth re-consent when real Google integration testing resumes.
- Supply/licence the mobile font binaries needed for clean-clone builds.
- Decide whether Flutter tests should join the default selector loop and which
  Flutter version is canonical.
- Decide the long-term default-branch and `.github` layout.
- Preserve any machine-specific Android SDK setup as local-only configuration.

## 8. Session recovery notes

- Session identity is machine-authored. Never infer an ID, select a transcript,
  or hand-write `current.json`, `close-marker.json`, or recovery JSON.
- Use `scripts/claude_session_state.py` for all lifecycle transitions. The
  historical filename is a compatibility detail shared by both clients.
- The reusable full-verification record belongs to work commit `6bbf0b4` and
  the current lifecycle identity; verify it with the helper before reuse.
- SessionStart may report degraded state; if it does, re-derive branch, HEAD,
  upstream, tree status, HANDOFF freshness, and marker state before work.
- The PREPARE transition must occur only after the closing documentation commit.
  FINALIZE remains a separate, owner-authorized post-push transition.
