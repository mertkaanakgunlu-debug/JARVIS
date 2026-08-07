// chatStream.js -- the shared SSE reader for /chat/stream, /chat/upload and
// /chat/confirm/{id}. External review (2026-07-23) named 9 scenarios this
// parser had no automated coverage for at all (no JS test infra existed in
// this repo before this file); all 9 are pinned here against a REAL
// ReadableStream, not a hand-rolled mock of fetch's Response shape.
import { describe, expect, it } from 'vitest'
import { readChatSse } from './chatStream'

// Builds a minimal fetch-Response-shaped object from a raw byte string,
// splitting it into arbitrary chunk sizes to prove the reader's line
// buffering survives frames split across read() boundaries -- not just
// "one chunk per line", which would hide a buffering bug.
function fakeResponse(raw, { ok = true, status = 200, chunkSize = 7 } = {}) {
  const enc = new TextEncoder()
  const bytes = enc.encode(raw)
  const chunks = []
  for (let i = 0; i < bytes.length; i += chunkSize) chunks.push(bytes.slice(i, i + chunkSize))
  return {
    ok,
    status,
    body: new ReadableStream({
      start(controller) {
        for (const c of chunks) controller.enqueue(c)
        controller.close()
      },
    }),
    async text() {
      return raw
    },
  }
}

function sse(frames) {
  return frames.map((f) => `data: ${f}\n\n`).join('')
}

describe('readChatSse', () => {
  it('accumulates plain tokens, unescaping \\n, until [DONE]', async () => {
    const out = await readChatSse(fakeResponse(sse(['Merhaba', ' d\\u00fcnya\\nikinci sat\\u0131r'.replace(/\\u00fc/g, 'ü').replace(/\\u0131/g, 'ı'), '[DONE]'])))
    expect(out.text).toBe('Merhaba dünya\nikinci satır')
    expect(out.confirmation).toBeNull()
    expect(out.error).toBeNull()
    expect(out.asyncTask).toBeNull()
  })

  it('parses a structured confirmation_required frame and keeps any pre-gate text', async () => {
    const frame = JSON.stringify({
      type: 'confirmation_required', id: 'c-123',
      payload: { tools: [{ name: 'gmail', description: 'Send an email to a@b.c' }], count: 1 },
    })
    const out = await readChatSse(fakeResponse(sse(['Bir saniye', frame, '[DONE]'])))
    expect(out.confirmation).toEqual({
      id: 'c-123', payload: { tools: [{ name: 'gmail', description: 'Send an email to a@b.c' }], count: 1 },
    })
    expect(out.text).toBe('Bir saniye')
  })

  it('captures a SECOND confirmation_required frame in the same stream', async () => {
    // resume_and_stream()'s own re-emitted marker for a second same-turn
    // interrupt -- App.jsx's resolveConfirmation() swaps the prompt in
    // place when this happens; the reader must surface it, not the first.
    const first = JSON.stringify({ type: 'confirmation_required', id: 'c-1', payload: { tools: [] } })
    const second = JSON.stringify({ type: 'confirmation_required', id: 'c-2', payload: { tools: [{ name: 'google_calendar' }] } })
    const out = await readChatSse(fakeResponse(sse([first, second, '[DONE]'])))
    expect(out.confirmation.id).toBe('c-2')
  })

  it('parses an [ERROR] frame', async () => {
    const out = await readChatSse(fakeResponse(sse(['[ERROR] boom'])))
    expect(out.error).toBe('boom')
  })

  it('parses an async-divert frame', async () => {
    const out = await readChatSse(fakeResponse(sse([JSON.stringify({ async: true, task_id: 't-9' }), '[DONE]'])))
    expect(out.asyncTask).toEqual({ async: true, task_id: 't-9' })
  })

  it('treats a JSON-lookalike token with no known shape as plain text', async () => {
    const out = await readChatSse(fakeResponse(sse(['{"foo": 1}', '[DONE]'])))
    expect(out.text).toBe('{"foo": 1}')
    expect(out.confirmation).toBeNull()
    expect(out.asyncTask).toBeNull()
  })

  it('fires onToken once per token as it streams', async () => {
    const seen = []
    await readChatSse(fakeResponse(sse(['a', 'b', '[DONE]'])), { onToken: (t) => seen.push(t) })
    expect(seen).toEqual(['a', 'b'])
  })

  // ── completion-contract TTFB: progress + final_answer frames ──────────

  it('parses a progress frame into out.progress and fires onProgress, never into out.text', async () => {
    const frame = JSON.stringify({ type: 'progress', phase: 'preparing_required_output', kind: 'chart' })
    const seen = []
    const out = await readChatSse(
      fakeResponse(sse([frame, 'İşte grafiğiniz.', '[DONE]'])),
      { onProgress: (p) => seen.push(p) },
    )
    expect(out.progress).toEqual({ phase: 'preparing_required_output', kind: 'chart' })
    expect(out.text).toBe('İşte grafiğiniz.')
    expect(seen).toEqual([{ phase: 'preparing_required_output', kind: 'chart' }])
  })

  it('a progress frame with no kind leaves kind undefined, not a crash', async () => {
    const frame = JSON.stringify({ type: 'progress', phase: 'resuming_required_output' })
    const out = await readChatSse(fakeResponse(sse([frame, '[DONE]'])))
    expect(out.progress.phase).toBe('resuming_required_output')
    expect(out.progress.kind).toBeUndefined()
  })

  it('a final_answer frame REPLACES out.text instead of appending to the draft', async () => {
    // The exact regression this closes: before this fix, final_answer had
    // no case at all and fell through to the plain-text branch, so the
    // correction was appended as literal JSON onto the draft it corrects.
    const frame = JSON.stringify({ type: 'final_answer', text: 'gerçek cevap' })
    const out = await readChatSse(fakeResponse(sse(['taslak cevap', frame, '[DONE]'])))
    expect(out.text).toBe('gerçek cevap')
    expect(out.text).not.toContain('taslak')
    expect(out.text).not.toContain('final_answer')
  })

  it('fires onFinal with the authoritative text when a final_answer frame arrives', async () => {
    const frame = JSON.stringify({ type: 'final_answer', text: 'gerçek cevap' })
    const seen = []
    await readChatSse(
      fakeResponse(sse(['taslak', frame, '[DONE]'])),
      { onFinal: (t) => seen.push(t) },
    )
    expect(seen).toEqual(['gerçek cevap'])
  })

  it('progress and final_answer do not shadow confirmation_required or async', async () => {
    const progress = JSON.stringify({ type: 'progress', phase: 'preparing_required_output' })
    const confirm = JSON.stringify({ type: 'confirmation_required', id: 'c-1', payload: { tools: [] } })
    const out = await readChatSse(fakeResponse(sse([progress, confirm, '[DONE]'])))
    expect(out.progress).toEqual({ phase: 'preparing_required_output', kind: undefined })
    expect(out.confirmation).toEqual({ id: 'c-1', payload: { tools: [] } })
  })

  // ── the two review-found robustness gaps ──────────────────────────────

  it('reports a non-2xx JSON error body as out.error instead of a silent empty success', async () => {
    const resp = fakeResponse(JSON.stringify({ detail: 'invalid API key' }), { ok: false, status: 401 })
    const out = await readChatSse(resp)
    expect(out.error).toBe('HTTP 401: invalid API key')
    expect(out.text).toBe('')
  })

  it('reports a non-2xx PLAIN TEXT error body (not JSON) too', async () => {
    const resp = fakeResponse('Internal Server Error', { ok: false, status: 500 })
    const out = await readChatSse(resp)
    expect(out.error).toBe('HTTP 500: Internal Server Error')
  })

  it('flushes a final line that never got a trailing newline', async () => {
    // The stream closes mid-frame -- no \n\n after the last "data: " line.
    // Before the fix this content was silently dropped once done=true.
    const raw = 'data: [DONE]' // deliberately no trailing \n\n
    const out = await readChatSse(fakeResponse(raw))
    // [DONE] flushed via the tail path must still end the read cleanly with
    // whatever text preceded it (none here) — the point is it's not lost.
    expect(out.error).toBeNull()
  })

  it('flushes a final un-terminated confirmation frame too', async () => {
    const frame = JSON.stringify({ type: 'confirmation_required', id: 'c-tail', payload: {} })
    const raw = `data: ${frame}` // no trailing newline at all — stream just ends
    const out = await readChatSse(fakeResponse(raw))
    expect(out.confirmation).toEqual({ id: 'c-tail', payload: {} })
  })

  it('reconstructs a JSON frame split across an arbitrary chunk boundary', async () => {
    const frame = JSON.stringify({ type: 'confirmation_required', id: 'c-split', payload: { tools: [{ name: 'shell_run' }] } })
    // chunkSize=1: every single byte is its own read() -- the most adversarial
    // possible split, including splitting mid-multibyte-UTF-8 sequence
    // (TextDecoder's {stream:true} mode is what makes this safe).
    const out = await readChatSse(fakeResponse(sse([frame, '[DONE]']), { chunkSize: 1 }))
    expect(out.confirmation.id).toBe('c-split')
    expect(out.confirmation.payload.tools[0].name).toBe('shell_run')
  })

  it('propagates a mid-stream read failure (network disconnect) to the caller', async () => {
    // The reader itself rejects, e.g. the connection dropped mid-response.
    // readChatSse must NOT swallow this into a silent {text:'', error:null}
    // — every real call site (HudPanels.jsx/App.jsx) wraps its own
    // fetch+readChatSse call in try/catch specifically to show "Connection
    // error"; a silently-eaten rejection here would defeat that.
    const resp = {
      ok: true,
      status: 200,
      body: new ReadableStream({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('data: partial'))
        },
        pull() {
          throw new Error('simulated disconnect')
        },
      }),
      async text() { return '' },
    }
    await expect(readChatSse(resp)).rejects.toThrow('simulated disconnect')
  })
})
