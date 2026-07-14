# HANDOFF.md — Where the last session left off

> Overwrite this file's content at the end of every session — it's meant to reflect only the
> *current* handoff state, not a history (that's what `git log` / `CHANGELOG.md` are for).

## Last session: 2026-07-14 (same-day continuation, fourth phase — Faz 3)

**Context:** Picked up directly from this same day's earlier sessions (Faz 0-2, all committed).
Implemented Faz 3 — real-time local voice — end to end, then, per the owner's decision mid-session
(asked via a scoping question), expanded it to also include genuine remote binary audio transport
over `/ws` plus an Electron client, rather than staying PC-local-only as originally bulleted in
ROADMAP.md.

**What happened this session (all uncommitted — see Git state below):**

1. **Local voice pipeline rebuilt** as new package `jarvis/voice/` (replaces the flat
   `jarvis/voice.py`, deleted): Silero-VAD (raw `.onnx` via `onnxruntime`) for end-of-turn
   detection, one-shot `faster-whisper` STT at end-of-turn (unchanged path, just relocated), local
   **Piper** TTS (`tr_TR-dfki-medium` + `en_US-lessac-medium`, `edge-tts` kept as fallback),
   full-duplex `sounddevice` I/O (`DuplexAudioIO`, callback-mode, replaces per-turn blocking
   record+play), and **barge-in** (sustained-high-confidence-speech gate during playback aborts
   audio + cancels the in-flight `agent.chat_stream()` task).
2. **A real, live-tested bug caught during this session, not assumed**: the Silero VAD model must
   stay pinned to **v5.1.2**, not the newer v6.2.1 — both tags export the identical I/O shape
   (`input`/`state`/`sr` → `output`/`stateN`) but v6.2.1's graph never produces a usable
   speech-probability signal with the standard streaming calling convention (max ~0.33 across a
   3-second spoken sentence; v5.1.2 scores the same audio at 0.85-0.93 mean). Full comparison
   method is in `jarvis/voice/vad.py`'s module docstring — re-verify with real audio before ever
   touching this pin.
3. **Faz 3's bundled bug fixes**: BUG-12 (critic draft+revision concatenation — fixed by tracking
   `metadata["langgraph_step"]` in `graph_stream_to_text()`), BUG-13 (`chat_stream()`/
   `resume_and_stream()` only caught `GraphInterrupt`, dropping barge-in-cancelled turns silently —
   now propagate `CancelledError` while still guaranteeing `event_bus.state("idle")`), BUG-23
   (openwakeword buffer never reset — now calls `Model.reset()` per session), plus a wiring gap
   (`--api --voice` without `--wakeword` previously started zero voice).
4. **Scope expanded mid-session (owner decision)** to also build remote binary audio transport over
   the existing `/ws` connection — `RemoteWsAudioIO` satisfies the same `AudioIO` protocol as the
   local backend (deliberately a *thin transport* interface — no duplicated VAD/STT/TTS logic
   between the two), so `RealtimeVoiceEngine` is fully transport-blind. Session arbitration
   (`jarvis/voice/session_manager.py`, first-claim-wins) + auto-pause of the local wakeword loop
   (Electron always spawns the backend with `--wakeword`) + a shared, lazily-loaded `VoiceModels`
   singleton (`get_shared_voice_models()`) so the local loop and remote sessions don't each reload
   Whisper/VAD/Piper. Full wire protocol written to new `docs/VOICE_PROTOCOL.md`.
5. **Electron HUD**: `useRemoteAudioSession` (new hook) + `pcm-capture-worklet.js` (new
   `AudioWorkletProcessor`) let the HUD act as mic/speaker via `getUserMedia`/Web Audio — no new
   npm dependencies. Spacebar now toggles this session instead of the old fire-and-forget PTT POST
   (that endpoint is untouched, still serves the local path). Security fix pulled forward from the
   Faz 8 backlog (**BUG-elec**): Electron now sends `?token=` on `/ws` (reads `JARVIS_API_KEY` from
   the same `.env` the backend reads); the server also refuses `audio_session_start` when no API
   key is configured at all.
6. **Two real bugs caught during my own final review pass** (after the "it all passes" verification
   round, re-reading the trickiest code once more before considering this done):
   - **Barge-in was silently dropping the first ~0.4s of the user's interrupting speech** — the
     frames that built up confidence for the barge-in gate were fed only to that gate, never
     accumulated anywhere, so by the time `BargeIn` fired and normal turn-tracking resumed, that
     window was gone. Fixed with a small rolling tail buffer (`jarvis/voice/engine.py`) seeded into
     the turn buffer the moment barge-in fires, plus a new `VadTurnSegmenter.force_speaking()`
     (`vad.py`) so the segmenter resumes from an already-speaking state instead of waiting for its
     own (redundant) speech-start detection. Verified with a scripted test isolating exactly this
     timing (`verify_bargein_tail.py` in scratchpad, not committed).
   - **A second `audio_session_start` on the same `/ws` connection while one was already active
     would silently leak the previous engine/task** (`try_claim` would just re-succeed since the
     connection already owns the claim, and the code would overwrite the tracked task/audio_io
     without tearing down the old ones first). Fixed with an explicit guard in `jarvis/api.py`'s
     `_start_audio_session`, rejecting the duplicate with `nack: "bad_request"`.

## Verification performed (all isolated / no real user data touched)

**Mechanically verified, with real (not mocked) components wherever the environment allowed —
this environment turned out to have real audio hardware, so more was actually testable than a
typical headless sandbox:**
- `SustainedGate`/`VadTurnSegmenter` pure-logic transitions at exact scripted frame indices.
- **Real Silero VAD v5.1.2 inference** (downloaded live, not mocked) against real Piper-synthesized
  speech: English 0.85 mean / Turkish 0.93 mean speech-probability vs. silence 0.003-0.01 — this is
  what caught the v6.2.1 regression in the first place.
- **Real Piper synthesis** for both configured voices, producing sane non-silent audio; **real
  Whisper STT round-trip** of that same audio (correct transcript + correct language detection for
  both English and Turkish).
- **Real hardware smoke test**: `RealtimeVoiceEngine.load()` (all three models together) +
  `engine.start()`/`events()` against this machine's actual microphone for ~1.5s of ambient
  silence — zero false `SpeechStarted`/`FinalTranscript`/`BargeIn`, confirming the VAD threshold
  correctly rejects background noise. Also directly exercised `DuplexAudioIO`'s real
  `InputStream`/`OutputStream` open/close mechanics (capture only; playback was fed silence only —
  deliberately did not autonomously play audible test phrases through the user's speakers without
  them present to expect it).
- **Full Part B protocol**, via `starlette.testclient.TestClient` against the real FastAPI app (a
  fake agent stood in only to avoid depending on Ollama/cloud credentials being configured — the
  session/protocol logic itself is 100% real): session ack/busy-nack/re-claim-after-release,
  binary frame dispatch, unauthenticated rejection, and the double-start guard added during review.
- Scripted, deterministic isolation test for the barge-in tail-buffer fix (fake `AudioIO` yielding
  labeled frames, confirms exactly which frame indices survive into the transcribed buffer).
- Full-package compile + import check across every modified/new Python file — no circular imports.

**NOT verifiable in this environment — explicit hand-off, needed before calling this actually
done:**
- **All Electron/JS changes are unverified beyond careful manual reading** — this dev machine has
  no Node.js/npm on PATH (confirmed absent from both Bash and PowerShell; the bundled
  `electron.exe`/`esbuild.cmd` under `node_modules` couldn't be coaxed into a standalone
  interpreter either). Run `npm run dev` (or however this project normally launches Electron)
  before trusting any of: `useJarvisSocket.js`, `useRemoteAudioSession.js`,
  `pcm-capture-worklet.js`, `App.jsx`, `Widget.jsx`, `main/index.js`.
- Headphones test (self-hear quality, no feedback loop).
- Speaker + live barge-in test — actually interrupting JARVIS mid-sentence by speaking; the
  acoustic self-bleed false-positive risk is fundamentally a real-microphone-with-speakers concern
  that can't be simulated.
- Turkish pronunciation/prosody quality judgment (mechanical checks confirm non-silent, correctly-
  durationed, round-trips-through-Whisper audio — not whether it *sounds* good).
- Electron's `getUserMedia` permission grant + real round-trip audio quality/latency over that path.
- End-to-end first-audio latency with real device/driver latency included.

## Git state as of this session

- Branch: `langgraph-migration`, **not merged to `main`**.
- **Everything from this session is uncommitted.** `git status`: 18 modified files, 1 deletion
  (`jarvis/voice.py`), 4 new paths (`jarvis/voice/`, `docs/VOICE_PROTOCOL.md`,
  `electron/src/renderer/src/audio/`, `electron/.../hooks/useRemoteAudioSession.js`). This is a
  large diff (full new backend package + Electron changes) — review before committing; consider
  whether to split Part A (local engine) and Part B (remote transport) into separate commits given
  their different verification confidence levels (Part A hardware-tested live, Part B protocol-
  tested but Electron-side unverified).
- Still 21 stray `.claude/worktrees/*` directories from past sessions, untouched (still needs
  explicit go-ahead — destructive, Faz 8 territory; unrelated to this session).

## Recommended next steps (pick up here)

1. **Run the human hand-off checklist above** — this is the real gate before considering Faz 3
   done, not just merged code. In particular: `npm run dev` in `electron/` to catch any JS mistakes
   the lack of Node.js here couldn't, then actually talk to JARVIS via `--voice`, `--wakeword`, and
   the Electron spacebar toggle with real hardware.
2. Decide on commit strategy (see Git state above) and whether to merge/keep this on
   `langgraph-migration` before starting Faz 4.
3. **Faz 4 — Güvenlik Çekirdeği + Asenkron Araçlar** ([ROADMAP.md](ROADMAP.md)) is next per the
   roadmap — the full safety kernel, prerequisite for MCP/IoT/proactivity. Not started.
4. Mobile (Flutter) remote-audio client is an explicitly deferred fast-follow, not scheduled —
   `docs/VOICE_PROTOCOL.md` exists specifically so that doesn't require reverse-engineering
   Electron's implementation when it's eventually picked up.
5. Separately noticed, not fixed, not this session's scope: the Android app's always-on wake-word
   service + native overlay + Flutter MethodChannel bridge exist but are entirely disconnected
   (nothing calls `startWakeWordService()`; a SharedPreferences key mismatch breaks even the
   boot-autostart fallback). Pre-existing, unrelated to Faz 3.
6. Confirm whether the 21 stray worktrees/branches should be cleaned up (still deferred, still
   needs owner go-ahead — destructive).

## Environment checklist to resume work

```powershell
.\.venv\Scripts\Activate.ps1
ollama serve                                          # optional — local LLM brain + embeddings;
                                                       # NOT required for voice (Piper/Whisper/
                                                       # Silero are all independent of Ollama)
python -m jarvis --voice                              # local voice, continuous, barge-in enabled
python -m jarvis --voice --wakeword                   # local voice, "Hey JARVIS"-gated
python -m jarvis --api --wakeword                     # API mode + local voice + remote-audio path
                                                       # available to Electron over /ws
```

**New this session:** `pip install -r requirements.txt` now also needs `piper-tts` + `scipy`
(added to requirements.txt). First run of anything voice-related downloads: Silero VAD's `.onnx`
(~2MB, from a pinned GitHub commit) to `~/.cache/jarvis/silero_vad.onnx`, and Piper's two voice
models (~60MB total) to `~/.cache/jarvis/piper_voices/` — both automatic, no action needed.

**Electron:** this session's changes need `npm run dev` (or equivalent) run by the owner — this
Claude Code environment had no Node.js available to do that itself (see MEMORY.md).

If `pip install -r requirements.txt` hits `resolution-too-deep`, use `uv pip install -r
requirements.txt --python .\.venv\Scripts\python.exe` instead (Google Cloud SDK's loose transitive
pins).
