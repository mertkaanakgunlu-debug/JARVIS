/**
 * Widget.jsx — floating orb widget (always-on-top, ~220px).
 * Shown on wake-word / when JARVIS starts speaking.
 * Double-click opens the full HUD.
 */
import { useState, useEffect } from 'react'
import JarvisOrb, { VoiceBars } from './components/JarvisOrb'
import useJarvisSocket from './hooks/useJarvisSocket'
import { useFakeMic } from './hooks/useFakeData'
import './styles.css'

const ACCENT = '#22d3ee'

function applyWidgetAccent() {
  const root = document.documentElement
  root.style.setProperty('--hud-cyan',      ACCENT)
  root.style.setProperty('--hud-cyan-soft', '#67e8f9')
  root.style.setProperty('--hud-glow-soft', '0 0 8px rgba(34,211,238,.35)')
  root.style.setProperty('--hud-line',      'rgba(34,211,238,.55)')
  root.style.setProperty('--hud-ink',       '#d6f1f7')
  root.style.setProperty('--hud-ink-dim',   '#7ba9b3')
}

export default function Widget() {
  const [apiUrl, setApiUrl] = useState(null)

  useEffect(() => {
    applyWidgetAccent()
    window.jarvis?.onConfig(cfg => setApiUrl(cfg.apiUrl))
    if (!window.jarvis) setApiUrl('http://127.0.0.1:8000')
    return () => window.jarvis?.removeAllListeners('config')
  }, [])

  const { connected, state } = useJarvisSocket(apiUrl)
  const micLevel = useFakeMic(state)

  const stateLabel = {
    idle:      'STANDBY',
    listening: 'LISTENING…',
    speaking:  'SPEAKING',
    thinking:  'THINKING',
    working:   'WORKING',
  }[state] || 'STANDBY'

  const stateColor = {
    listening: '#22d3ee',
    speaking:  '#67e8f9',
    thinking:  '#a855f7',
    working:   '#fbbf24',
    idle:      '#7ba9b3',
  }[state] || '#7ba9b3'

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
      <JarvisOrb size={180} state={state} accent={ACCENT} micLevel={micLevel} />

      <div style={{
        marginTop: 6, fontSize: 9, letterSpacing: '.22em', textTransform: 'uppercase',
        color: stateColor, textShadow: `0 0 8px ${stateColor}`,
        fontFamily: '"Share Tech Mono", monospace',
      }}>
        {stateLabel}
      </div>

      {(state === 'listening' || state === 'speaking') && (
        <div style={{ marginTop: 4 }}>
          <VoiceBars state={state} accent={ACCENT} count={14} />
        </div>
      )}

      <div style={{
        position: 'absolute', bottom: 6, right: 10,
        width: 5, height: 5, borderRadius: '50%',
        background: connected ? '#22d3ee' : '#fbbf24',
        boxShadow: connected ? '0 0 4px #22d3ee' : '0 0 4px #fbbf24',
      }} />
    </div>
  )
}
