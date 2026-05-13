/**
 * App.jsx — Full JARVIS HUD.
 * Connects to FastAPI WebSocket, uses fake-data fallback when offline.
 * Accent color changes automatically based on JARVIS state:
 *   idle/listening → cyan (#22d3ee)
 *   thinking/working → yellow (#FFC857)
 *   speaking → red (#FF5577)
 */
import React, { useState, useEffect } from 'react'
import './styles.css'
import JarvisOrb, { OrbitalRings, VoiceBars } from './components/JarvisOrb'
import {
  TopBar, BottomBar, CenterCaption,
  CurrentTask, SubagentsPanel, SystemMetrics,
  CalendarPanel, ProgressToday, ProjectTracker,
  ActivityFeed, VaultPanel, Transcript,
} from './components/HudPanels'
import useJarvisSocket from './hooks/useJarvisSocket'
import useClock from './hooks/useClock'
import { useFakeMic, useFakeFeed, useFakeMetrics } from './hooks/useFakeData'

// ── Color helpers ─────────────────────────────────────────────────────────────
function toRgb(hex) {
  const h = hex.replace('#', '')
  const v = h.length === 3 ? h.split('').map(c => c + c).join('') : h
  return [parseInt(v.slice(0,2),16), parseInt(v.slice(2,4),16), parseInt(v.slice(4,6),16)]
}
function lighten(hex, amt) {
  const [r,g,b] = toRgb(hex)
  return `rgb(${Math.min(255,r+(255-r)*amt)|0},${Math.min(255,g+(255-g)*amt)|0},${Math.min(255,b+(255-b)*amt)|0})`
}
function darken(hex, amt) {
  const [r,g,b] = toRgb(hex)
  return `rgb(${(r*(1-amt))|0},${(g*(1-amt))|0},${(b*(1-amt))|0})`
}
function applyAccent(accent, gridIntensity) {
  const root = document.documentElement
  const rgb  = toRgb(accent)
  root.style.setProperty('--hud-cyan',      accent)
  root.style.setProperty('--hud-cyan-soft', lighten(accent, 0.25))
  root.style.setProperty('--hud-cyan-deep', darken(accent, 0.4))
  root.style.setProperty('--hud-grid-rgb',  `${rgb[0]} ${rgb[1]} ${rgb[2]}`)
  root.style.setProperty('--hud-grid-alpha',String(gridIntensity))
  root.style.setProperty('--hud-line',     `rgba(${rgb[0]},${rgb[1]},${rgb[2]},.55)`)
  root.style.setProperty('--hud-line-dim', `rgba(${rgb[0]},${rgb[1]},${rgb[2]},.22)`)
  root.style.setProperty('--hud-glow',
    `0 0 12px rgba(${rgb[0]},${rgb[1]},${rgb[2]},.5), 0 0 32px rgba(${rgb[0]},${rgb[1]},${rgb[2]},.18)`)
  root.style.setProperty('--hud-glow-soft',`0 0 8px rgba(${rgb[0]},${rgb[1]},${rgb[2]},.35)`)
}

// ── State → accent color mapping ─────────────────────────────────────────────
const STATE_ACCENT = {
  idle:      '#22d3ee',   // cyan — free
  listening: '#22d3ee',   // cyan — free
  thinking:  '#FFC857',   // yellow — reasoning
  working:   '#FFC857',   // yellow — executing
  speaking:  '#FF5577',   // red — responding
}

const GRID_INTENSITY = 0.06

// ── Uptime counter ────────────────────────────────────────────────────────────
function useUptime() {
  const [s, setS] = useState(0)
  useEffect(() => { const id = setInterval(() => setS(p => p + 1), 1000); return () => clearInterval(id) }, [])
  const pad = n => n.toString().padStart(2,'0')
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = s%60
  return `${pad(h)}:${pad(m)}:${pad(sec)}`
}

// ── Static placeholder data (rich; shown when WS offline) ────────────────────
const PLACEHOLDER_EVENTS = [
  { time: '10:00', title: 'EE-302 · Lecture',           where: 'Hall B-204',      kind: 'live' },
  { time: '14:00', title: 'Office Hours · Prof. Yıldız', where: 'EE-411',          kind: 'idle' },
  { time: '17:30', title: 'Senior Project standup',      where: 'Discord · Voice', kind: 'idle' },
  { time: '21:00', title: 'Gym',                         where: 'Campus rec',      kind: 'idle' },
  { time: '23:59', title: 'Physics Lab Report · DUE',    where: 'Submit · Moodle', kind: 'red'  },
]

const PLACEHOLDER_PROJECTS = [
  { title: 'EE-302 · Differential Eq · PSet 3',  progress: 84, due: '+ 2d 04h', stage: 'Compiling LaTeX',    tag: 'homework'  },
  { title: 'Senior · Mark VII Web Dashboard',     progress: 47, due: '+ 12d',    stage: 'FastAPI · WebSocket',tag: 'project'   },
  { title: 'PHYS-201 · Lab Report',               progress: 92, due: '+ 06h',    stage: 'Final review',       tag: 'homework'  },
  { title: 'Vault migration → Obsidian sync',     progress: 30, due: '— soon',   stage: 'Designing schema',   tag: 'internal'  },
]

const PLACEHOLDER_VAULT_ENTRIES = [
  { title: 'favorite editor → Neovim',         tag: 'memory', ts: '5d ago'    },
  { title: 'Mark VII · architecture sketch',   tag: 'note',   ts: 'today'     },
  { title: 'Laplace transforms · cheatsheet',  tag: 'note',   ts: 'yesterday' },
  { title: 'Conversation · 2026-05-09',         tag: 'convo',  ts: '1d ago'   },
  { title: 'report · em-pset2.pdf',            tag: 'report', ts: '3d ago'    },
]

const TASK_BY_STATE = {
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

const TRANSCRIPT_BY_STATE = {
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

export default function App() {
  const [apiUrl, setApiUrl] = useState(null)
  const [accent, setAccent]   = useState(STATE_ACCENT.idle)

  // Get API URL from Electron main process
  useEffect(() => {
    window.jarvis?.onConfig(cfg => setApiUrl(cfg.apiUrl))
    // Fallback for browser dev mode
    if (!window.jarvis) setApiUrl('http://127.0.0.1:8000')
    return () => window.jarvis?.removeAllListeners('config')
  }, [])

  // Live data from WebSocket
  const { connected, state, transcript, feedLines, task, metrics, calEvents, vaultData, progress } =
    useJarvisSocket(apiUrl)

  // State → accent: changes color palette when JARVIS switches modes
  useEffect(() => {
    const newAccent = STATE_ACCENT[state] || STATE_ACCENT.idle
    setAccent(newAccent)
    applyAccent(newAccent, GRID_INTENSITY)
  }, [state])

  // Initial accent application on mount
  useEffect(() => {
    applyAccent(accent, GRID_INTENSITY)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Fake data fallback (active when disconnected)
  const fakeMic      = useFakeMic(state)
  const fakeFeed     = useFakeFeed(state)
  const fakeMetrics  = useFakeMetrics(state)

  const micLevel = connected ? (metrics.micLevel ?? fakeMic) : fakeMic
  const feed     = connected && feedLines.length ? feedLines : fakeFeed
  const met      = connected ? metrics : fakeMetrics

  const clock  = useClock()
  const uptime = useUptime()

  const activeAgents = state === 'thinking' ? ['math', 'writer']
    : state === 'working'   ? ['coder', 'shell']
    : state === 'speaking'  ? ['vault']
    : []

  // Use live data when connected, rich placeholders when offline
  const calendarEvents  = connected && calEvents.length    ? calEvents    : PLACEHOLDER_EVENTS
  const projects        = PLACEHOLDER_PROJECTS  // TODO: real project data from backend
  const vaultEntries    = connected && vaultData.entries.length ? vaultData.entries : PLACEHOLDER_VAULT_ENTRIES
  const vaultCount      = connected ? vaultData.count : 2847
  const displayTask     = (connected && task.name) ? task : TASK_BY_STATE[state] || TASK_BY_STATE.idle
  const displayTranscript = (connected && transcript.length) ? transcript : (TRANSCRIPT_BY_STATE[state] || [])

  // Progress: use WS data or placeholders
  const cloudSpend = progress.cloudSpend ?? '0.47'
  const costSaved  = progress.costSaved  ?? '4.18'

  return (
    <>
      <div className="hud-grid" />
      <div className="hud-scan" />

      <div className="hud-stage" data-density="comfy" data-layout="default">

        {/* Top bar */}
        <TopBar state={state} clock={clock} onClose={() => window.jarvis?.hideHud()} />

        {/* Left column */}
        <div className="slot-l1" style={{ display: 'flex', minHeight: 0 }}>
          <CurrentTask state={state} taskName={displayTask.name} steps={displayTask.steps} />
        </div>
        <div className="slot-l2" style={{ display: 'flex', minHeight: 0 }}>
          <SubagentsPanel active={activeAgents} />
        </div>
        <div className="slot-l3" style={{ display: 'flex', minHeight: 0 }}>
          <SystemMetrics
            cpu={met.cpu} gpu={met.gpu} ram={met.ram} vram={met.vram}
            mic={Math.round(micLevel * 100)}
            voice={state === 'speaking' ? Math.round(micLevel * 100) : 0}
            model={state === 'thinking' ? 'Gemini 2.5 Pro' : 'Gemini 2.5 Flash'}
            latency={met.latency}
          />
        </div>

        {/* Center — orb */}
        <div className="slot-c center-stage">
          <div style={{ position: 'relative', width: 560, height: 560,
            display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <OrbitalRings size={560} accent={accent} />
            <JarvisOrb size={420} state={state} accent={accent} micLevel={micLevel} />
          </div>
          <CenterCaption state={state} name={displayTask.name} />
          <div style={{ display: 'flex', gap: 18, alignItems: 'center', marginTop: 4 }}>
            <span className="dim" style={{ fontSize: 9, letterSpacing: '.22em' }}>VOICE I/O</span>
            <VoiceBars state={state} accent={accent} count={28} />
            <span className="dim numeric" style={{ fontSize: 10 }}>{Math.round(micLevel * 100)}%</span>
          </div>
          {/* Connection status dot */}
          <div style={{ position: 'absolute', bottom: 8, right: 8, fontSize: 9,
            color: connected ? 'var(--hud-cyan)' : 'var(--hud-amber)',
            letterSpacing: '.14em', textTransform: 'uppercase' }}>
            {connected ? '● LIVE' : '○ OFFLINE'}
          </div>
        </div>

        {/* Right column */}
        <div className="slot-r1" style={{ display: 'flex', minHeight: 0 }}>
          <CalendarPanel today={clock.date} events={calendarEvents} />
        </div>
        <div className="slot-r2" style={{ display: 'flex', minHeight: 0 }}>
          <ProgressToday
            jobsDone={progress.jobsDone || 11} jobsTotal={progress.jobsTotal || 14}
            runtime={progress.runtime || uptime}
            tokensIn={progress.tokensIn || 184320} tokensOut={progress.tokensOut || 62870}
            cloudSpend={cloudSpend} costSaved={costSaved}
          />
        </div>
        <div className="slot-r3" style={{ display: 'flex', minHeight: 0 }}>
          <ProjectTracker projects={projects} />
        </div>

        {/* Center-bottom strip */}
        <div className="slot-cb center-bottom-strip">
          <div style={{ display: 'flex', minHeight: 0 }}>
            <ActivityFeed lines={feed} />
          </div>
          <div style={{ display: 'flex', minHeight: 0 }}>
            <VaultPanel entries={vaultEntries} chromaCount={vaultCount} />
          </div>
          <div style={{ display: 'flex', minHeight: 0 }}>
            <Transcript turns={displayTranscript} typing={state === 'speaking' || state === 'listening'} />
          </div>
        </div>

        <BottomBar
          state={state} micLevel={micLevel} latency={met.latency}
          vaultCount={vaultCount} uptime={uptime}
        />
      </div>

      {/* Float orb removed — the separate widget window serves this purpose */}
    </>
  )
}
