---
handoff_schema: 1
branch: langgraph-migration
covered_through_sha: 8ab0b10633a52feb44416a9a190ae5e7dda230db
---

# HANDOFF — current state

Single-state snapshot, rewritten at each session close. It is not history:
`git log` and `CHANGELOG.md` own that. If this document conflicts with the
repository, the repository is right and this file is the bug. The
`covered_through_sha` is the last work commit described here, never the closing
documentation commit.

## 1. Current verified state

- The active branch is `langgraph-migration`. At preparation time on
  2026-08-11, local and remote `main` both resolved to `5f6f6ff` and remained
  untouched. `langgraph-migration` was seven commits ahead of its remote.
  Derive changing branch relationships live.
- Work commit `8ab0b10` closes the reproducible empty-STT capture failure:
  every segmented turn now emits a terminal transcript event, including an
  empty Whisper result, and one-shot PTT/wake-word capture re-arms without
  dispatching an empty agent turn.
- Capture diagnostics now provide an activation-local evidence ladder across
  device callbacks, PortAudio status/overflow, queue consumption/backlog,
  frame contract, VAD, segmentation, buffered turn, and STT result.
- No VAD threshold, model setting, sample-rate contract, remote-voice path,
  TTS, NVIDIA NIM behavior, or proactive behavior changed.
- Push authority remains owner-only. Derive remote state live before any future
  finalize action.

## 2. Last completed work

Commit `8ab0b10` (`fix(voice): rearm capture after empty transcripts`) completed
the deterministic localization and minimal reliability fix:

- A synthetic regression reproduced the defect: VAD detected and ended a real
  turn, the audio buffer reached STT, Whisper returned an empty string, and the
  engine emitted no terminal event. A finite one-shot session returned
  `ended`; a continuously open PTT/wake-word session therefore stayed active
  until later audio, presenting as a missed utterance.
- The engine now emits `FinalTranscript` for every completed STT attempt. The
  session treats a blank transcript as a terminal capture result, does not call
  the agent, and returns `turn_complete` for one-shot capture so the activation
  is safely re-armed.
- Per-activation counters and recent signal evidence now distinguish callback
  delivery, status/overflow, consumed frames, queue high-water/backlog,
  frame-size mismatch, VAD frames/probability, speech start/end, STT attempt,
  and non-empty STT. `/voice-status` exposes the aggregate ladder without
  rendering raw audio.
- Regression coverage pins the empty-STT terminal behavior, success/silence
  ladder boundaries, activation counters, frame mismatch detection, and the
  diagnostics API/CLI fields.

### Localization disposition

- Deterministic fixture evidence eliminates the VAD/segmentation/buffer seam
  for the committed speech fixtures: real Silero produced one speech start,
  one speech end, and one STT attempt for each fixture, with peak probabilities
  from `0.982042` to `0.995552`.
- A two-second aggregate-only microphone smoke delivered and consumed `63`
  correctly sized frames with queue high-water `1`, no callback status,
  overflow, backlog, or frame mismatch. Real Silero processed all `63` frames.
  No raw audio was retained and no Whisper/model workload was run.
- These observations prove the current idle-window device-to-Silero path, not
  the cause of a historical user utterance. Historical misses predate the new
  stage counters, so they cannot be attributed retroactively to microphone,
  PortAudio, queue, VAD, segmentation, buffer, or Whisper without a new event.
- The empty-STT control-flow defect is reproducible and fixed. Any remaining
  missed-utterance report must be localized from the new per-activation ladder
  before thresholds or model settings change.

## 3. Operational modes and rollout decisions

- Approval provenance, confirmation, safety, idempotency, execution-contract,
  required-output, and Result Binding behavior are unchanged.
- Mobile confirmation continuity behavior from `30071bc` remains intact.
- No real Gmail, Calendar, Drive, or other external write was used.

## 4. Tests and CI

Deterministic evidence collected on 2026-08-11:

- The new empty-STT regression failed before the fix with `ended !=
  turn_complete`, then passed after the fix.
- Focused voice tests reported `156 passed, 1 warning`.
- Repository selector
  `.venv\Scripts\python.exe scripts\dev_verify.py --base f4bd800225ef6ba3957f0d070743b0362ad21009 --run`
  selected targeted verification, not full fallback. Diff check and Ruff
  passed; pytest reported `221 passed, 5 deselected, 5 warnings`.
- The five `voice_e2e` tests were deselected by the repository configuration;
  no real Whisper benchmark was run while the machine was under unrelated
  game/model load.
- The first canonical full run had one unrelated Ollama embedding
  `ReadTimeout`; its isolated retry passed. The successful rerun recorded
  exact-tree evidence for `8ab0b10`: diff check and Ruff passed; pytest reported
  `3526 passed, 5 deselected, 410 warnings in 798.21s`.
- `git diff --check` passed.

## 5. Known open issues

- A future real missed utterance still needs immediate stage-ladder capture to
  determine whether any residual failure is device/PortAudio/queue, VAD and
  segmentation, or Whisper. The historical symptom cannot be localized more
  exactly from evidence that was never recorded.
- Mobile and Electron still do not persist and send a stable per-client
  `conversation_id` for every newly initiated chat/upload turn.
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

Run one short, coordinated real-spoken PTT capture and inspect the stage ladder
immediately if an utterance is missed. Replay retained test-safe audio through
`WavAudioIO` only with owner consent; change VAD/STT behavior only after the
counter boundary proves the responsible layer.

## 7. Human-required actions

- Coordinate the short real-spoken PTT run for residual voice localization.
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
- The reusable full-verification record belongs to work commit `8ab0b10`, branch
  `langgraph-migration`, and the current lifecycle identity. Verify it with the
  helper before attempting to reuse it.
- A correctly prepared close has exactly one documentation commit after the
  covered work SHA. Derive marker and repository state live rather than trusting
  remembered remote or CI state.
