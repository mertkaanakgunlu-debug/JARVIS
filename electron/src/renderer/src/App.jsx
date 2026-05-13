/**
 * App.jsx — Full JARVIS HUD.
 * Connects to FastAPI WebSocket, uses fake-data fallback when offline.
 */
import { useState, useEffect } from 'react'
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

// ── Color helpers (for accent derivation) ────────────────────────────────────
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

// ── Uptime counter ────────────────────────────────────────────────────────────
function useUptime() {
  const [s, setS] = useState(0)
  useEffect(() => { const id = setInterval(() => setS(p => p + 1), 1000); return () => clearInterval(id) }, [])
  const pad = n => n.toString().padStart(2,'0')
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), sec = s%60
  return `${pad(h)}:${pad(m)}:${pad(sec)}`
}

// ── HUD settings (accent color, density, etc.) ────────────────────────────────
const DEFAULT_SETTINGS = { accent: '#22d3ee', density: 'comfy', layout: 'default', gridIntensity: 0.06 }

// ── Static placeholder data (shown when live data not yet received) ────────────
const PLACEHOLDER_PROJECTS = [
  { title: 'JARVIS — Electron HUD',       progress: 65, due: 'active',  stage: 'Faz 11 implementation', tag: 'project' },
  { title: 'Vault migration → sync',       progress: 30, due: '— soon',  stage: 'Designing schema',       tag: 'internal' },
]
const PLACEHOLDER_EVENTS = [
  { time: '--:--', title: 'Loading calendar…', where: '', kind: 'idle' },
]

export default function App() {
  const [apiUrl, setApiUrl] = useState(null)
  const [settings, setSettings] = useState(DEFAULT_SETTINGS)

  // Get API URL from Electron main process
  useEffect(() => {
    window.jarvis?.onConfig(cfg => setApiUrl(cfg.apiUrl))
    // Fallback for browser dev mode
    if (!window.jarvis) setApiUrl('http://127.0.0.1:8000')
    return () => window.jarvis?.removeAllListeners('config')
  }, [])

  // Apply accent on settings change
  useEffect(() => {
    applyAccent(settings.accent, settings.gridIntensity)
  }, [settings.accent, settings.gridIntensity])

  // Live data from WebSocket
  const { connected, state, transcript, feedLines, task, metrics, calEvents, vaultData, progress } =
    useJarvisSocket(apiUrl)

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

  const calendarEvents = connected && calEvents.length ? calEvents : PLACEHOLDER_EVENTS
  const projects       = PLACEHOLDER_PROJECTS  // TODO: real project data from backend

  return (
    <>
      <div className="hud-grid" />
      <div className="hud-scan" />

      <div className="hud-stage" data-density={settings.density} data-layout={settings.layout}>

        {/* Top bar */}
        <TopBar state={state} clock={clock} onClose={() => window.jarvis?.hideHud()} />

        {/* Left column */}
        <div className="slot-l1" style={{ display: 'flex', minHeight: 0 }}>
          <CurrentTask state={state} taskName={task.name} steps={task.steps} />
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
            <OrbitalRings size={560} accent={settings.accent} />
            <JarvisOrb size={420} state={state} accent={settings.accent} micLevel={micLevel} />
          </div>
          <CenterCaption state={state} name={task.name} />
          <div style={{ display: 'flex', gap: 18, alignItems: 'center', marginTop: 4 }}>
            <span className="dim" style={{ fontSize: 9, letterSpacing: '.22em' }}>VOICE I/O</span>
            <VoiceBars state={state} accent={settings.accent} count={28} />
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
            jobsDone={progress.jobsDone} jobsTotal={progress.jobsTotal}
            runtime={progress.runtime || uptime}
            tokensIn={progress.tokensIn} tokensOut={progress.tokensOut}
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
            <VaultPanel entries={vaultData.entries} chromaCount={vaultData.count} />
          </div>
          <div style={{ display: 'flex', minHeight: 0 }}>
            <Transcript turns={transcript} typing={state === 'speaking' || state === 'listening'} />
          </div>
        </div>

        <BottomBar
          state={state} micLevel={micLevel} latency={met.latency}
          vaultCount={vaultData.count} uptime={uptime}
        />
      </div>
    </>
  )
}
