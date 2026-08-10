---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 30071bce83e7a08fb609e9fb27c53c1560e725b2
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
- Work commit `30071bc` closes mobile confirmation continuity. Confirmation
  ownership metadata now survives the server event, app-wide pending state,
  tab navigation, and the approve/deny request.
- Pending confirmations are visible from every mobile tab through a small CORE
  indicator. Completion and expiry clear the same ID-checked app-wide state;
  internal confirmation and conversation IDs are never rendered.
- Existing server decision, safety, and idempotency semantics remain intact.
  Mobile's submit guard and server-side single-use claim still prevent duplicate
  execution on double taps, duplicate delivery, or navigation.
- Push authority remains owner-only. Derive remote state live before any future
  finalize action.

## 2. Last completed work

Commit `30071bc` (`fix(mobile): preserve confirmation continuity across tabs`)
completed the continuity fix:

- Confirmation SSE, WebSocket, and structured `/chat` surfaces now carry the
  pinned `conversation_id` and approval TTL as top-level transport metadata.
  `/chat/confirm/{id}` receives the same conversation ID from mobile.
- The backend emits an observation-only `confirmation_closed` event when a
  prompt is claimed, missing, or expires. It does not approve, deny, retry, or
  alter the graph's decision path.
- The app-scoped Riverpod confirmation state now owns an ID-checked expiry
  timer and consumes close events. The bottom navigation displays a small,
  accessible pending dot on CORE while another tab is active.
- Regression coverage pins metadata parsing, API forwarding, cross-tab
  visibility, expiry cleanup, no internal-ID rendering, and preserved
  exactly-once behavior.

## 3. Operational modes and rollout decisions

- Approval provenance, policy gating, pending-confirmation claiming, and
  exactly-once execution behavior are unchanged.
- `required_outputs_mode`, execution-contract rollout modes, and approve-side
  Result Binding semantics are unchanged.
- The new close event is UI lifecycle metadata only; the server remains the
  authority for whether a confirmation can be resolved.
- No real Gmail, Calendar, Drive, or other external write was used.

## 4. Tests and CI

Deterministic evidence collected on 2026-08-10:

- Focused backend/API command passed `70` tests with `12` warnings.
- Focused mobile confirmation/model/provider/widget command passed `41` tests.
  `flutter analyze` reported no issues. The locally available ignored font
  assets allowed this run; `MOBILE-ASSETS-01` remains a clean-clone issue.
- Repository selector
  `.venv\Scripts\python.exe scripts\dev_verify.py --base 502fa70855b60235e1cb6cff0c94de69d1b3fbe1 --run`
  selected targeted verification, not full fallback. Diff check and Ruff
  passed; its Python selection reported `588 passed, 53 warnings`, and its
  mobile analyzer check reported no issues.
- After the work commit, `.venv\Scripts\python.exe scripts\dev_verify.py --full`
  passed and recorded exact-tree evidence for `30071bc`: diff check and Ruff
  passed; pytest reported `3523 passed, 5 deselected, 410 warnings in 749.17s`.
- No live model workload or external-service write was run.

## 5. Known open issues

- Mobile and Electron still do not persist and send a stable per-client
  `conversation_id` for every newly initiated chat/upload turn; this completed
  work only preserves the backend-pinned ID from a confirmation event through
  its decision request.
- `MOBILE-ASSETS-01`: a clean clone lacks the gitignored font binaries required
  for a full mobile build/test. The wake-word ONNX model is also absent.
- `chromadb` remains unpinned. Flutter defaults/version pinning and full
  selector coverage remain incomplete.
- Voice input capture reliability remains the highest-priority open voice P0;
  the instrumentation exists, but the real utterance-drop mechanism is not
  localized.
- Source-binding coverage and mail/calendar live evidence remain deliberately
  scoped; the real mailbox path was not exercised here.
- `python_run` is confirmation-gated but not sandboxed. Proactive L2 behavior
  still relies on prompt-level mitigation.
- `.Codex/worktrees/*` contains historical scratch worktrees with commits not
  all reachable from `langgraph-migration`; do not remove them without owner
  approval.

## 6. Next engineering priority

Close the voice input capture reliability P0. Capture a real dropped utterance
with the existing diagnostics, replay it through `WavAudioIO`, and localize the
failure to the VAD/STT side or the device/PortAudio/mic-gating side before
changing behavior.

## 7. Human-required actions

- Provide a real dropped-utterance capture or coordinate a live microphone run
  for the voice P0 localization.
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
- The reusable full-verification record belongs to work commit `30071bc`, branch
  `langgraph-migration`, and the current lifecycle identity. Verify it with the
  helper before attempting to reuse it.
- A correctly prepared close has exactly one documentation commit after the
  covered work SHA. Derive marker and repository state live rather than trusting
  remembered remote or CI state.
