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


// ── Static demo fixtures (shown ONLY when the WebSocket is disconnected) ─────
//
// Moved here from App.jsx on 2026-07-31 so that every invented value in the HUD
// lives in one file whose name says what it is. They were previously mixed in
// with real rendering code, which is how a hardcoded "Routing to Gemini 2.5
// Pro" line and a vault count of 2847 stayed in the product long after the
// project went local-first.
//
// Reachable only on the `connected === false` branch, where the badge reads
// "○ OFFLINE · DEMO DATA". Nothing here may be rendered while connected —
// tests/test_no_synthetic_live_data.py enforces that.
export const PLACEHOLDER_EVENTS = [
  { time: '10:00', title: 'EE-302 · Lecture',           where: 'Hall B-204',      kind: 'live' },
  { time: '14:00', title: 'Office Hours · Prof. Yıldız', where: 'EE-411',          kind: 'idle' },
  { time: '17:30', title: 'Senior Project standup',      where: 'Discord · Voice', kind: 'idle' },
  { time: '21:00', title: 'Gym',                         where: 'Campus rec',      kind: 'idle' },
  { time: '23:59', title: 'Physics Lab Report · DUE',    where: 'Submit · Moodle', kind: 'red'  },
]

export const PLACEHOLDER_PROJECTS = [
  { title: 'EE-302 · Differential Eq · PSet 3',  progress: 84, due: '+ 2d 04h', stage: 'Compiling LaTeX',    tag: 'homework'  },
  { title: 'Senior · Mark VII Web Dashboard',     progress: 47, due: '+ 12d',    stage: 'FastAPI · WebSocket',tag: 'project'   },
  { title: 'PHYS-201 · Lab Report',               progress: 92, due: '+ 06h',    stage: 'Final review',       tag: 'homework'  },
  { title: 'Vault migration → Obsidian sync',     progress: 30, due: '— soon',   stage: 'Designing schema',   tag: 'internal'  },
]

export const PLACEHOLDER_VAULT_ENTRIES = [
  { title: 'favorite editor → Neovim',         tag: 'memory', ts: '5d ago'    },
  { title: 'Mark VII · architecture sketch',   tag: 'note',   ts: 'today'     },
  { title: 'Laplace transforms · cheatsheet',  tag: 'note',   ts: 'yesterday' },
  { title: 'Conversation · 2026-05-09',         tag: 'convo',  ts: '1d ago'   },
  { title: 'report · em-pset2.pdf',            tag: 'report', ts: '3d ago'    },
]

export const TASK_BY_STATE = {
  idle: {
    name: '—',
    steps: [],
  },
  listening: {
    name: 'Awaiting voice input…',
    steps: [
      { label: "Wake-word detected · 'Jarvis…'",  done: true,   t: '00:00.04' },
      { label: 'Faster-Whisper STT streaming',     active: true, t: '00:01.12' },
      { label: 'Intent classification' },
    ],
  },
  thinking: {
    name: 'EE-302 · Differential Equations · PSet 3',
    steps: [
      { label: 'Parse PDF problem set',             done: true,   t: '00:01.20' },
      { label: 'Extract 6 problems via pdf.read',   done: true,   t: '00:02.84' },
      { label: 'Delegate Q1–Q4 to MathAgent',       done: true,   t: '00:04.12' },
      { label: 'Q5 — Laplace transform · Gemini',   active: true, t: '00:14.07' },
      { label: 'Compose LaTeX report' },
      { label: 'Compile PDF · pdflatex' },
    ],
  },
  speaking: {
    name: "Briefing · today's schedule",
    steps: [
      { label: 'Recall vault/notes/calendar.md',   done: true,   t: '00:00.18' },
      { label: 'Compose response (Gemini Flash)',   done: true,   t: '00:00.44' },
      { label: 'TTS · edge-tts · streaming',        active: true, t: '00:01.92' },
    ],
  },
  working: {
    name: 'Compile report → em-pset3.pdf',
    steps: [
      { label: 'report.write → em-pset3.tex',      done: true,   t: '00:08.41' },
      { label: 'pdflatex pass 1',                   done: true,   t: '00:11.06' },
      { label: 'pdflatex pass 2 (cross-refs)',      active: true, t: '00:13.80' },
      { label: 'Move to vault/reports/' },
    ],
  },
}

export const TRANSCRIPT_BY_STATE = {
  idle: [
    { who: 'j', text: 'All systems nominal. Three projects active, two with deadlines this week. Shall I begin?' },
  ],
  listening: [
    { who: 'j', text: 'Welcome back, sir. How can I be of service?' },
    { who: 'u', text: '' },
  ],
  thinking: [
    { who: 'u', text: '/think solve problem 5 from the differential equations pset I uploaded yesterday' },
    { who: 'j', text: 'Routing to Gemini 2.5 Pro — the Laplace inverse on this one needs partial fractions. Working on it.' },
  ],
  speaking: [
    { who: 'u', text: "what's on the agenda today" },
    { who: 'j', text: "Three items, sir. EE-302 office hours at fourteen hundred, your physics lab report is due at twenty-three fifty-nine, and Mertcan asked you to call back regarding the senior project — I've left the relevant notes in the vault." },
  ],
  working: [
    { who: 'u', text: 'render the EM pset to PDF and drop it in reports' },
    { who: 'j', text: 'Compiling. Two passes for the cross-references. Estimated thirty-two seconds.' },
  ],
}
