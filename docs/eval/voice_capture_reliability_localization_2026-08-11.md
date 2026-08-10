# Voice input capture reliability localization — 2026-08-11

## Disposition

A deterministic dropped-turn path was reproduced and fixed, but the exact
historical microphone incident cannot be reconstructed because no audio or
stage counters were captured during it.

The reproducible path was:

1. microphone audio reaches the engine;
2. VAD starts and ends a turn;
3. the captured buffer reaches Whisper;
4. Whisper returns an empty string;
5. the engine emits no terminal transcript event;
6. PTT/wakeword never returns `turn_complete`, so the same activation stays
   open until the user speaks again.

The failing regression observed `ended` instead of `turn_complete` with a
finite deterministic WAV transport. A real continuously-open microphone would
wait rather than reach `ended`, which is the user-visible "first utterance was
not taken" behavior. The fix always emits the terminal `FinalTranscript`, even
when its text is blank; the session driver suppresses an agent turn for blank
text but completes one-shot PTT/wakeword capture normally.

## Stage evidence

The `/voice/status` snapshot now exposes a per-activation stage ladder without
recording audio or transcript content:

`PortAudio callback -> queue consumer -> VAD frame -> speech start -> turn end
-> STT attempt -> non-empty STT result`

It also reports callback/consumed sample counts, initial and high-water queue
depth, frame-size mismatches, activation-local status/overflow counts, the
configured VAD threshold, recent RMS/VAD maxima, and whether the last STT result
contained text. These values distinguish the requested layers as follows:

| Layer | Evidence that reaches or stops at this layer |
|---|---|
| microphone/device capture | activation has zero callbacks, or callbacks advance |
| PortAudio callback/overflow | activation-local status and real-overflow counters |
| frame queue/backlog | callback vs consumed counts; initial/current/high-water depth |
| sample-rate/frame-size contract | configured 16 kHz plus callback and engine mismatch counters |
| Silero VAD | VAD frame count and recent VAD max/mean beside the configured threshold |
| turn segmentation | speech-start and turn-end counts |
| captured audio buffer | last turn duration/sample path before STT |
| Whisper STT | attempts, non-empty results, duration, and last-result-had-text boolean |

## What was eliminated and what remains

A two-second aggregate-only local microphone smoke (no audio retained) observed
63 callbacks, 63 consumed 512-sample frames, queue initial/high-water `0/1`, no
frame mismatch, no PortAudio status or overflow, and 63 real Silero frames.
Ambient input peaked at RMS `0.001175`; Silero peaked at `0.043006`, below the
unchanged `0.5` threshold, so no speech start was expected. This proves the
device -> PortAudio -> queue -> frame-contract -> Silero path worked during that
smoke window only; it does not eliminate a historical transient.

The committed deterministic WAV fixtures all crossed real Silero and completed
segmentation: `evet.wav`, `evet_onayliyorum.wav`, `gule_gule.wav`, and
`hayir.wav` each produced one speech start, one turn end, and one STT attempt;
their VAD maxima were `0.982042`, `0.993698`, `0.995023`, and `0.995552`.
Whisper was replaced with a no-op fixture in this check, so these numbers prove
VAD/segmentation/buffer delivery, not transcription quality.

The exact old incident therefore remains ambiguous between a transient local
capture fault and an empty/mis-decoded real Whisper result. The newly fixed
empty-result control-flow bug is reproducible. The next missed live utterance
should be followed immediately by `/voice-status`; the first stage whose count
did not advance localizes it without changing a threshold or retaining speech.

No VAD threshold, silence duration, frame size, sample rate, Whisper model,
beam size, language lock, or device preference was changed. Remote voice, TTS,
NVIDIA NIM, proactive behavior, and large live benchmarks were not exercised.
