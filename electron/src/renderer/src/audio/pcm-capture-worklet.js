/**
 * pcm-capture-processor — AudioWorkletProcessor that converts the microphone's
 * Float32 samples to Int16 PCM and posts them to the main thread in small
 * (~20ms) chunks, as transferable ArrayBuffers (zero-copy).
 *
 * Runs inside an AudioContext constructed at 16000Hz (see
 * useRemoteAudioSession.js) — Chromium resamples the getUserMedia track to
 * that rate transparently before this processor ever sees a sample, so no
 * resampling happens here.
 */

const CHUNK_SAMPLES = 320 // 20ms @ 16kHz

class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this._pending = []
    this._pendingLength = 0
  }

  process(inputs) {
    const input = inputs[0]
    if (input && input.length > 0) {
      const channelData = input[0]
      if (channelData && channelData.length > 0) {
        this._pending.push(channelData.slice())
        this._pendingLength += channelData.length
        this._flush()
      }
    }
    return true // keep the processor alive for the life of the node
  }

  _flush() {
    while (this._pendingLength >= CHUNK_SAMPLES) {
      const merged = new Float32Array(this._pendingLength)
      let offset = 0
      for (const part of this._pending) {
        merged.set(part, offset)
        offset += part.length
      }

      const toSend = merged.subarray(0, CHUNK_SAMPLES)
      const remainder = merged.subarray(CHUNK_SAMPLES)

      const int16 = new Int16Array(toSend.length)
      for (let i = 0; i < toSend.length; i++) {
        const s = Math.max(-1, Math.min(1, toSend[i]))
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff
      }
      this.port.postMessage(int16.buffer, [int16.buffer])

      this._pending = remainder.length > 0 ? [remainder] : []
      this._pendingLength = remainder.length
    }
  }
}

registerProcessor('pcm-capture-processor', PcmCaptureProcessor)
