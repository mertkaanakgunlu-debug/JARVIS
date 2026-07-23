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
 */
export async function readChatSse(resp, { onToken } = {}) {
  const out = { text: '', confirmation: null, asyncTask: null, error: null }
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
  return out
}
