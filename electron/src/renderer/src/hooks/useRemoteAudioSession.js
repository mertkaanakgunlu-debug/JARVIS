/**
 * useRemoteAudioSession — Faz 3: lets the Electron HUD act as the microphone/
 * speaker for a JARVIS conversation, using the server's Whisper/Piper instead
 * of on-device engines. Talks to the backend over the SAME /ws connection
 * useJarvisSocket already owns — pass this hook's handleAudioChunk/
 * handleAudioControl into useJarvisSocket's onAudioChunk/onAudioControl
 * options, and pass useJarvisSocket's sendRaw into this hook's start()/stop()
 * at call time (see App.jsx) — see docs/VOICE_PROTOCOL.md for the wire format.
 *
 * Capture: getUserMedia -> an AudioContext constructed AT 16000Hz directly
 * (Chromium resamples the track transparently before any JS sees a sample —
 * no resampling code needed here, unlike the server-side fallback which exists
 * for a future client that can't force 16kHz at capture) -> AudioWorkletNode
 * (pcm-capture-worklet.js) -> ~20ms Int16 PCM frames sent as binary WS frames.
 *
 * Playback: a SEPARATE AudioContext, (re)constructed at whatever rate the
 * server's audio_format message declares (Piper/edge-tts voices don't all
 * share one native rate) — incoming binary frames are scheduled back-to-back
 * through a GainNode, so audio_playback_stop (barge-in) can ramp the gain down
 * over ~8ms instead of an abrupt, audibly-clicky stop().
 */
import { useCallback, useEffect, useRef, useState } from 'react'

const CAPTURE_SAMPLE_RATE = 16000
const STOP_RAMP_S = 0.008

// sendRaw is passed to start()/stop() at CALL time, not taken as a hook
// argument -- App.jsx needs to pass this hook's handleAudioChunk/
// handleAudioControl INTO useJarvisSocket(), which is where sendRaw comes
// from, so this hook can't depend on sendRaw at construction time without a
// circular initialization order between the two hooks.
export default function useRemoteAudioSession() {
  const [active, setActive] = useState(false)
  const [sessionState, setSessionState] = useState('idle') // idle | starting | active | error

  const captureCtxRef  = useRef(null)
  const workletRef     = useRef(null)
  const micStreamRef   = useRef(null)

  const playbackCtxRef      = useRef(null)
  const playbackRateRef     = useRef(null)
  const gainNodeRef         = useRef(null)
  const nextStartTimeRef    = useRef(0)
  const scheduledSourcesRef = useRef([])

  const stopCapture = useCallback(() => {
    workletRef.current?.disconnect()
    workletRef.current = null
    if (captureCtxRef.current) {
      captureCtxRef.current.close().catch(() => {})
      captureCtxRef.current = null
    }
    if (micStreamRef.current) {
      micStreamRef.current.getTracks().forEach(t => t.stop())
      micStreamRef.current = null
    }
  }, [])

  const stopPlayback = useCallback(() => {
    const ctx = playbackCtxRef.current
    const gain = gainNodeRef.current
    if (ctx && gain) {
      const now = ctx.currentTime
      gain.gain.cancelScheduledValues(now)
      gain.gain.setValueAtTime(gain.gain.value, now)
      gain.gain.linearRampToValueAtTime(0, now + STOP_RAMP_S)
      gain.gain.setValueAtTime(1, now + STOP_RAMP_S + 0.01) // restored for the next utterance
    }
    for (const src of scheduledSourcesRef.current) {
      try { src.stop() } catch (_) { /* already finished/stopped */ }
    }
    scheduledSourcesRef.current = []
    nextStartTimeRef.current = ctx ? ctx.currentTime : 0
  }, [])

  const closePlaybackContext = useCallback(() => {
    scheduledSourcesRef.current = []
    if (playbackCtxRef.current) {
      playbackCtxRef.current.close().catch(() => {})
      playbackCtxRef.current = null
    }
    gainNodeRef.current = null
    playbackRateRef.current = null
  }, [])

  const handleAudioChunk = useCallback((arrayBuffer) => {
    const rate = playbackRateRef.current
    if (!rate) return // no audio_format announcement yet -- drop (shouldn't happen in practice)

    let ctx = playbackCtxRef.current
    if (!ctx || ctx.sampleRate !== rate) {
      if (ctx) ctx.close().catch(() => {})
      ctx = new AudioContext({ sampleRate: rate })
      const gain = ctx.createGain()
      gain.connect(ctx.destination)
      gainNodeRef.current = gain
      playbackCtxRef.current = ctx
      nextStartTimeRef.current = ctx.currentTime
    }

    const int16 = new Int16Array(arrayBuffer)
    const float32 = new Float32Array(int16.length)
    for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 0x8000

    const buffer = ctx.createBuffer(1, float32.length, rate)
    buffer.copyToChannel(float32, 0)

    const src = ctx.createBufferSource()
    src.buffer = buffer
    src.connect(gainNodeRef.current)

    const startAt = Math.max(ctx.currentTime, nextStartTimeRef.current)
    src.start(startAt)
    nextStartTimeRef.current = startAt + buffer.duration
    scheduledSourcesRef.current.push(src)
    src.onended = () => {
      scheduledSourcesRef.current = scheduledSourcesRef.current.filter(s => s !== src)
    }
  }, [])

  const handleAudioControl = useCallback((msg) => {
    switch (msg.type) {
      case 'audio_session_ack':
        setSessionState('active')
        break
      case 'audio_session_nack':
        console.warn('[voice] remote audio session rejected:', msg.reason, msg.detail || '')
        setSessionState('error')
        setActive(false)
        stopCapture()
        break
      case 'audio_session_end':
        setSessionState('idle')
        setActive(false)
        stopCapture()
        closePlaybackContext()
        break
      case 'audio_format':
        playbackRateRef.current = msg.sample_rate
        break
      case 'audio_playback_stop':
        stopPlayback()
        break
      default:
        break
    }
  }, [stopCapture, stopPlayback, closePlaybackContext])

  const start = useCallback(async (sendRaw) => {
    if (active || sessionState === 'starting') return
    setSessionState('starting')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      })
      micStreamRef.current = stream

      const ctx = new AudioContext({ sampleRate: CAPTURE_SAMPLE_RATE })
      captureCtxRef.current = ctx
      await ctx.audioWorklet.addModule(new URL('../audio/pcm-capture-worklet.js', import.meta.url))

      const source = ctx.createMediaStreamSource(stream)
      const worklet = new AudioWorkletNode(ctx, 'pcm-capture-processor')
      worklet.port.onmessage = (ev) => { sendRaw(ev.data) }
      source.connect(worklet)
      workletRef.current = worklet

      const sent = sendRaw({ type: 'audio_session_start', sample_rate: CAPTURE_SAMPLE_RATE, format: 'pcm_s16le', channels: 1 })
      if (!sent) throw new Error('WebSocket not connected')
      setActive(true)
    } catch (err) {
      console.error('[voice] failed to start remote audio session:', err)
      setSessionState('error')
      stopCapture()
    }
  }, [active, sessionState, stopCapture])

  const stop = useCallback((sendRaw) => {
    sendRaw?.({ type: 'audio_session_stop' })
    stopCapture()
    stopPlayback()
    closePlaybackContext()
    setActive(false)
    setSessionState('idle')
  }, [stopCapture, stopPlayback, closePlaybackContext])

  // Unmount safety net -- don't leave the mic hot or an AudioContext open.
  useEffect(() => () => { stopCapture(); closePlaybackContext() }, [stopCapture, closePlaybackContext])

  return { active, sessionState, start, stop, handleAudioChunk, handleAudioControl }
}
