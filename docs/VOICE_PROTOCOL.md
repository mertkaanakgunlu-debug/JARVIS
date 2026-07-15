# Voice Protocol — Remote Audio over `/ws` (Faz 3)

> Written so a future client (the mobile fast-follow, or any other remote client) can implement
> this without reverse-engineering it from Electron's JS. The server side lives in
> `jarvis/voice/io_remote_ws.py` (`RemoteWsAudioIO`) and `jarvis/api.py`'s `/ws` handler. The only
> implemented client today is the Electron HUD (`electron/src/renderer/src/hooks/
> useRemoteAudioSession.js` + `useJarvisSocket.js`).

## Why this exists

`GET /ws` already carries JSON telemetry (state, transcript, metrics, …) to the HUD. Faz 3 adds a
second capability on the **same connection**: a remote client can act as the microphone/speaker
for a full JARVIS conversation — using the server's Whisper (STT) and Piper (TTS) instead of
on-device engines. This lets a client that isn't physically at the PC (a phone over Tailscale, or
the Electron HUD, which today happens to run on the same machine as the backend but doesn't have
to) drive a real-time voice conversation.

This is deliberately **not** a new endpoint or a new connection — JSON control messages and binary
PCM audio frames are multiplexed on one `/ws` connection, using WebSocket's own frame typing
(text vs. binary) to tell them apart.

## Transport basics

- One `/ws` connection = at most one audio session, and at most one audio session process-wide
  (see **Arbitration** below).
- **Binary** frames carry audio: raw **PCM16LE mono**, no header, no sequence number. One frame =
  one WebSocket binary message. There's no reordering/loss to guard against — a WebSocket rides one
  ordered, reliable TCP stream — so a sequence number would only earn its keep over a genuinely
  lossy/unordered transport (e.g. if a future client used raw UDP/RTP instead). Add one then, not
  speculatively now.
- **Text** frames carry JSON control messages (below), sharing the connection with the pre-existing
  telemetry event types (`state`, `message`, `metrics`, …) — check `"type"` to dispatch.
- Direction disambiguates "kind" without needing a tag inside the frame: client→server binary is
  always mic audio; server→client binary is always synthesized speech.

## Authentication

Same as the connection itself — an `X-API-Key` header on the `/ws` upgrade request (preferred;
mobile uses this — see `mobile/lib/core/ws_client.dart`) or a `?token=<JARVIS_API_KEY>` query param
(kept for Electron, whose renderer uses the browser `WebSocket` API and cannot set custom headers
on the upgrade request; the header wins if both are present — see `jarvis/api.py`'s `ws_endpoint`).
A query-string token is visible to anything that logs URLs (proxies, access logs, OS/browser
connection history) even though the header option isn't itself encryption — this server has no TLS
termination, so wire-level confidentiality on an untrusted network still depends on tunneling
through Tailscale rather than exposing this port directly (Faz 8, BUG-mob-tls). **On top of that**,
starting an audio session additionally requires `JARVIS_API_KEY` to be configured at all
server-side; `audio_session_start` gets `nack: "unauthenticated"` if it's empty. Once this socket
can carry live mic audio and synthesized speech, an unauthenticated connection is a materially
bigger deal than the read-only telemetry it carried before Faz 3 — remote audio is
opt-in-by-configuration, not available with zero setup.

## Control messages

```jsonc
// client → server
{"type": "audio_session_start", "sample_rate": 16000, "format": "pcm_s16le", "channels": 1}
{"type": "audio_session_stop"}

// server → client
{"type": "audio_session_ack", "session_id": "remote-ws-<id>"}
{"type": "audio_session_nack", "reason": "busy" | "bad_request" | "unauthenticated", "detail": "..."}
{"type": "audio_session_end", "reason": "client_requested" | "connection_closed"}
{"type": "audio_format", "sample_rate": 22050}   // announced before the first chunk of an utterance,
                                                  // and again whenever it changes — see below
{"type": "audio_playback_stop"}                  // barge-in: stop playing NOW, discard anything buffered
```

### Why there's no `tts_sample_rate` in `audio_session_ack`

An earlier draft of this protocol put `tts_sample_rate` in the ack, assuming one fixed rate for
the whole session. That's wrong: the rate depends on *which* voice ends up speaking a given
utterance (Piper's `tr`/`en` voices both happen to be 22050Hz today, but a different language or
the `edge-tts` cloud fallback can differ — edge-tts typically outputs 24000Hz). The server doesn't
know the rate for the *first* utterance until it's actually about to synthesize it, which is after
the ack. Instead, `audio_format` is sent immediately before the first binary chunk of an utterance
whenever the rate differs from the last one announced (see `RemoteWsAudioIO.play_chunk`) — build
the playback `AudioContext` at whatever rate the most recent `audio_format` declared, and be ready
to rebuild it if a later utterance announces a different one.

## Mic direction (client → server)

Send **16kHz mono PCM16LE**, ideally in ~20-40ms chunks (Electron's implementation uses 20ms /
320 samples). Getting to 16kHz is the client's job in the common case: construct the *capture*
`AudioContext` at 16000Hz directly (`new AudioContext({sampleRate: 16000})`) — Chromium (and other
browsers) resample the live audio track to that rate transparently before any JS ever sees a
sample, so no client-side resampling code is needed.

The server-side fallback (`scipy.signal.resample_poly`, in `RemoteWsAudioIO.push_mic_frame`) exists
for a client that *can't* force 16kHz at capture — declare your real capture rate honestly in
`audio_session_start`'s `sample_rate` field and the server resamples; don't lie about the rate to
avoid implementing capture-rate control, that's more fragile than just declaring it correctly.

## TTS direction (server → client)

Chunked to ~100ms pieces (not a whole sentence at once) specifically so **barge-in stays
responsive** — if the abort flag flips mid-sentence, at most ~100ms of already-decided audio is
still in flight, not a multi-second sentence. On `audio_playback_stop`: stop playback immediately.
If you're using Web Audio `AudioBufferSourceNode` scheduling (as Electron does), don't call
`.stop()` bluntly — ramp a `GainNode` down over ~5-10ms first, or the abrupt cut is audibly clicky.

Don't pre-buffer/schedule far ahead of "now" — schedule incoming chunks back-to-back just slightly
ahead of the audio clock, and keep references to every currently-scheduled-but-unfinished source so
`audio_playback_stop` can actually reach and stop all of them.

## Barge-in

The server's VAD runs on **incoming mic frames** exactly the same way whether the audio source is
local hardware or a remote client (see `jarvis/voice/engine.py` — `RealtimeVoiceEngine` is
transport-blind). If sustained speech is detected while the server is sending TTS audio, it stops
synthesizing/sending further chunks and sends `audio_playback_stop`. There is no client-side
pre-emptive "duck audio if I think I hear myself talking" logic today — over a LAN/localhost
connection (Electron) the round trip is a few ms, so server-authoritative stop is responsive
enough. **This is the first thing to revisit for the mobile fast-follow**: real cellular/Tailscale
RTT is much higher than LAN, and a client-side local energy-threshold pre-mute (while waiting for
the server's authoritative stop) would likely be worth adding then. Not needed for Electron.

## Session arbitration

First-claim-wins, process-wide (`jarvis/voice/session_manager.py`): the local wakeword/PTT loop and
at most one remote client can hold the "active session" claim at a time. A second claimant gets
`audio_session_nack: "busy"`. Electron's main process always spawns the backend with `--wakeword`
(`electron/src/main/index.js`) — starting a remote session automatically pauses the local loop's
*next* claim (`pause_local_voice()`/`resume_local_voice()` in `jarvis/voice_api.py`; also exposed
manually via `POST /voice/local/pause`/`/resume` for testing) so the two don't fight over the
shared agent/conversation state. This only arbitrates *within one process* — it doesn't prevent a
separately-invoked `python -m jarvis --voice` in another terminal from also touching the same
on-disk session state; that's a pre-existing, separate multi-invocation hazard, not this
arbitration's job.

## What a new client needs to implement

1. Connect to `/ws?token=<key>` (existing telemetry connection — reuse it, don't open a second one).
2. On mic-button-press: send `audio_session_start`, wait for `audio_session_ack`/`nack`.
3. Capture mic audio at 16kHz mono PCM16LE (or declare your real rate and rely on server-side
   resampling), send as binary frames, ~20-40ms each.
4. On incoming binary frames: decode as PCM16LE mono at the **most recently announced**
   `audio_format.sample_rate` and play them back-to-back.
5. On `audio_playback_stop`: stop/clear playback immediately.
6. On mic-button-press again (or user-initiated stop): send `audio_session_stop`, stop capturing.
7. Handle `audio_session_end`/disconnect by tearing down capture/playback cleanly either way.

## Known limitation

No true acoustic echo cancellation anywhere in this design — barge-in relies on the server's VAD
requiring *sustained, high-confidence* speech (not any blip) specifically to reduce false triggers
from the assistant's own voice bleeding from speakers back into an open mic. This is a pragmatic
mitigation, not a fix; using headphones on the capturing device sidesteps the problem entirely.
