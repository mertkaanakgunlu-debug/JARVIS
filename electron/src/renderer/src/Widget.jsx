/**
 * Widget.jsx — floating orb widget (always-on-top, ~220px).
 * Shown on wake-word / when JARVIS starts speaking.
 * Double-click opens the full HUD.
 * Accent color matches main HUD state palette:
 *   idle/listening → cyan, thinking/working → yellow, speaking → red
 */
import React, { useState, useEffect } from 'react'
import JarvisOrb, { VoiceBars } from './components/JarvisOrb'
import useJarvisSocket from './hooks/useJarvisSocket'
import { useFakeMic } from './hooks/useFakeData'
import './styles.css'

// ── State → accent palette (mirrors App.jsx) ──────────────────────────────────
const STATE_ACCENT = {
  idle:      '#22d3ee',
  listening: '#22d3ee',
  thinking:  '#FFC857',
  working:   '#FFC857',
  speaking:  '#FF5577',
}

function toRgb(hex) {
  const h = hex.replace('#', '')
  const v = h.length === 3 ? h.split('').map(c => c + c).join('') : h
  return [parseInt(v.slice(0,2),16), parseInt(v.slice(2,4),16), parseInt(v.slice(4,6),16)]
}
function lighten(hex, amt) {
  const [r,g,b] = toRgb(hex)
  return `rgb(${Math.min(255,r+(255-r)*amt)|0},${Math.min(255,g+(255-g)*amt)|0},${Math.min(255,b+(255-b)*amt)|0})`
}

function applyWidgetAccent(accent) {
  const root = document.documentElement
  const [r,g,b] = toRgb(accent)
  root.style.setProperty('--hud-cyan',      accent)
  root.style.setProperty('--hud-cyan-soft', lighten(accent, 0.25))
  root.style.setProperty('--hud-glow-soft', `0 0 8px rgba(${r},${g},${b},.35)`)
  root.style.setProperty('--hud-line',      `rgba(${r},${g},${b},.55)`)
  root.style.setProperty('--hud-ink',       '#d6f1f7')
  root.style.setProperty('--hud-ink-dim',   '#7ba9b3')
}

export default function Widget() {
  const [apiUrl, setApiUrl] = useState(null)
  const [apiKey, setApiKey] = useState('')
  const [accent, setAccent] = useState(STATE_ACCENT.idle)

  useEffect(() => {
    // Transparent background — the Electron window has transparent:true but
    // styles.css sets body{background:#000}. Override it here for widget mode.
    document.body.style.background = 'transparent'
    document.documentElement.style.background = 'transparent'
    applyWidgetAccent(accent)
    window.jarvis?.onConfig(cfg => { setApiUrl(cfg.apiUrl); setApiKey(cfg.apiKey || '') })
    if (!window.jarvis) setApiUrl('http://127.0.0.1:8000')
    return () => window.jarvis?.removeAllListeners('config')
  }, [])

  const { connected, state } = useJarvisSocket(apiUrl, apiKey)
  const micLevel = useFakeMic(state)

  // Update accent when state changes
  useEffect(() => {
    const newAccent = STATE_ACCENT[state] || STATE_ACCENT.idle
    setAccent(newAccent)
    applyWidgetAccent(newAccent)
  }, [state])

  const stateLabel = {
    idle:      'STANDBY',
    listening: 'LISTENING…',
    speaking:  'SPEAKING',
    thinking:  'THINKING',
    working:   'WORKING',
  }[state] || 'STANDBY'

  return (
    <div
      onDoubleClick={() => window.jarvis?.openHudFromWidget()}
      title="Double-click to open JARVIS HUD"
      style={{
        width: 220, height: 220,
        display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center',
        cursor: 'pointer', background: 'transparent', userSelect: 'none',
        position: 'relative',
      }}
    >
      <JarvisOrb size={180} state={state} accent={accent} micLevel={micLevel} />

      <div style={{
        marginTop: 6, fontSize: 9, letterSpacing: '.22em', textTransform: 'uppercase',
        color: accent, textShadow: `0 0 8px ${accent}`,
        fontFamily: '"Share Tech Mono", monospace',
      }}>
        {stateLabel}
      </div>

      {(state === 'listening' || state === 'speaking') && (
        <div style={{ marginTop: 4 }}>
          <VoiceBars state={state} accent={accent} count={14} />
        </div>
      )}

      <div style={{
        position: 'absolute', bottom: 6, right: 10,
        width: 5, height: 5, borderRadius: '50%',
        background: connected ? accent : '#fbbf24',
        boxShadow: connected ? `0 0 4px ${accent}` : '0 0 4px #fbbf24',
      }} />
    </div>
  )
}
