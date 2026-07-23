/**
 * chatStream.js — the ONE SSE reader for every /chat* streaming endpoint
 * (/chat/stream, /chat/upload, /chat/confirm/{id}).
 *
 * Server frame vocabulary (jarvis/api.py's _sse_frames):
 *   data: <token>                                   plain text, \n escaped as \\n
 *   data: {"type":"confirmation_required",id,payload}   structured approval prompt
 *   data: {"async":true,"task_id":...}              query diverted to a background task
 *   data: [ERROR] <message>
 *   data: [DONE]
 *
 * Before this module each call site re-implemented the parse loop and NONE of
 * them recognized the structured frames — a confirmation_required frame (added
 * server-side in Faz 7.3) rendered as raw JSON in the transcript and the
 * approval round-trip was impossible from the HUD. Mirrors the backend's own
 * "_sse_frames is shared by every endpoint so the next omission is
 * structurally impossible" remediation, client-side.
 *
 * Returns { text, confirmation, asyncTask, error } — exactly one of
 * confirmation/asyncTask/error may be set; text is whatever streamed before
 * the stream ended (may be non-empty alongside a confirmation: the model can
 * talk before hitting the gate). onToken (optional) fires per token for
 * incremental rendering.
 *
 * Two robustness gaps closed here (external review, 2026-07-23), both of
 * which previously made a real failure look like a silent, empty success:
 *   - resp.ok was never checked. A non-2xx response (auth failure, FastAPI's
 *     422 validation error, ...) is a JSON/plain body, not an SSE stream —
 *     none of its lines start with "data: ", so every one was just dropped
 *     and the caller got back {text: "", error: null}, indistinguishable
 *     from "the model answered with nothing".
 *   - a final line with no trailing \n (stream ends mid-frame, or the
 *     connection is aborted) stayed in `buf` and was silently discarded once
 *     the read loop saw `done`. _sse_frames() always terminates every real
 *     frame with \n\n in normal operation, so this only bit on the abnormal
 *     paths above -- exactly when the caller most needs to see what arrived.
 */
export async function readChatSse(resp, { onToken } = {}) {
  const out = { text: '', confirmation: null, asyncTask: null, error: null }
  if (!resp || !resp.ok) {
    out.error = await _describeErrorResponse(resp)
    return out
  }
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  const feed = (line) => {
    if (!line.startsWith('data: ')) return false
    const data = line.slice(6)
    if (data === '[DONE]') return true
    if (data.startsWith('[ERROR]')) { out.error = data.slice(7).trim(); return true }
    if (data.startsWith('{')) {
      // Structured frame — but a plain token could legitimately start with
      // "{", so only treat KNOWN shapes as structured; anything else falls
      // through as text.
      let obj = null
      try { obj = JSON.parse(data) } catch { obj = null }
      if (obj && obj.type === 'confirmation_required') {
        out.confirmation = { id: obj.id, payload: obj.payload || {} }
        return false // [DONE] still follows; keep reading
      }
      if (obj && obj.async) { out.asyncTask = obj; return false }
    }
    const token = data.replace(/\\n/g, '\n')
    out.text += token
    onToken?.(token)
    return false
  }
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    const lines = buf.split('\n')
    buf = lines.pop()
    for (const line of lines) {
      if (feed(line)) return out
    }
  }
  if (buf) feed(buf) // flush a final line the stream never newline-terminated
  return out
}

async function _describeErrorResponse(resp) {
  if (!resp) return 'no response (network error)'
  let body = ''
  try {
    body = await resp.text()
  } catch {
    // body unreadable — fall through with just the status
  }
  let detail = body
  if (body) {
    try {
      const obj = JSON.parse(body)
      detail = obj.detail || obj.message || body
    } catch {
      // not JSON — use the raw text as-is
    }
  }
  const status = resp.status ?? '?'
  return detail ? `HTTP ${status}: ${detail}` : `HTTP ${status}`
}
