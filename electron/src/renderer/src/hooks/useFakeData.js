/**
 * useFakeData — animated placeholder data used when WebSocket is not connected.
 * Mirrors the prototype's fake hooks exactly so the HUD looks alive from startup.
 */
import { useState, useEffect, useRef } from 'react'

export function useFakeMic(state) {
  const [v, setV] = useState(0.2)
  useEffect(() => {
    let raf, t0 = performance.now()
    function loop(now) {
      const t = (now - t0) / 1000
      let x
      if (state === 'speaking')
        x = 0.55 + 0.35 * (Math.sin(t * 8) * 0.5 + Math.sin(t * 17.3) * 0.35 + Math.sin(t * 31.1) * 0.15)
      else if (state === 'listening') x = 0.25 + 0.15 * Math.sin(t * 1.6)
      else if (state === 'thinking')  x = 0.45 + 0.2 * Math.sin(t * 4.2)
      else if (state === 'working')   x = 0.7 + 0.1 * Math.sin(t * 5.5)
      else x = 0.18 + 0.05 * Math.sin(t * 1.2)
      setV(Math.max(0, Math.min(1, x)))
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [state])
  return v
}

const FEED_LINES = [
  { kind: 'tool',  body: 'shell.run → git status · clean working tree' },
  { kind: 'local', body: 'Gemini 2.5 Flash · 1,247 tokens · 38 tok/s' },
  { kind: 'tool',  body: 'vault.write → notes/meeting-notes.md' },
  { kind: 'cloud', body: 'Gemini 2.5 Pro escalation · /think prefix detected' },
  { kind: 'note',  body: 'Memory: \'user prefers brief answers\' (recall hit)' },
  { kind: 'tool',  body: 'files.write → report.tex (2,184 B)' },
  { kind: 'tool',  body: 'web_search → tavily.search(\'latest news\')' },
  { kind: 'cloud', body: 'Gemini ↦ research synthesis · 4 sources, 1,892 toks' },
  { kind: 'tool',  body: 'report.compile → vault/reports/report.pdf · 312 KB' },
  { kind: 'note',  body: 'ChromaDB: 2,847 vectors · 14.2 MB on disk' },
]

export function useFakeFeed(state) {
  const [lines, setLines] = useState(() =>
    FEED_LINES.slice(0, 6).map((l, i) => ({
      ...l, id: i,
      ts: new Date(Date.now() - (5 - i) * 8000).toLocaleTimeString('en-GB', { hour12: false })
    }))
  )
  const idRef = useRef(6)
  useEffect(() => {
    const id = setInterval(() => {
      const tmpl = FEED_LINES[Math.floor(Math.random() * FEED_LINES.length)]
      setLines(p => [
        ...p,
        { ...tmpl, id: idRef.current++, ts: new Date().toLocaleTimeString('en-GB', { hour12: false }) }
      ].slice(-14))
    }, state === 'working' || state === 'thinking' ? 1600 : 3200)
    return () => clearInterval(id)
  }, [state])
  return lines
}

export function useFakeMetrics(state) {
  const [m, setM] = useState({ cpu: 28, gpu: 64, ram: 14.2, vram: 5.7, latency: 312 })
  useEffect(() => {
    const id = setInterval(() => {
      setM(p => {
        const drift = (a, lo, hi, d = 4) => Math.max(lo, Math.min(hi, a + (Math.random() - 0.5) * d))
        const heavy = state === 'thinking' || state === 'working'
        return {
          cpu:     drift(p.cpu,     heavy ? 50 : 18, heavy ? 88 : 42, heavy ? 8 : 4),
          gpu:     drift(p.gpu,     heavy ? 70 : 30, heavy ? 96 : 70, heavy ? 8 : 5),
          ram:     drift(p.ram,     11,  22, 0.4),
          vram:    drift(p.vram,    4.5, heavy ? 11 : 7, 0.3),
          latency: heavy
            ? Math.round(drift(p.latency, 600, 1100, 80))
            : Math.round(drift(p.latency, 180, 420, 40)),
        }
      })
    }, 1100)
    return () => clearInterval(id)
  }, [state])
  return m
}
