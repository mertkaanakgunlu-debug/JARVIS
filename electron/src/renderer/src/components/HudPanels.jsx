/**
 * HudPanels — all wireframe panel components for the JARVIS HUD.
 * Ported from the Claude Design prototype (hud-panels.jsx).
 */
import React, { useRef, useEffect } from 'react'

// ── Panel chrome ──────────────────────────────────────────────────────────────
export function Panel({ title, id, status = 'live', live = true, children, scroll = false }) {
  return (
    <section className="panel" style={{ width: '100%', height: '100%' }}>
      <header className="panel-hd">
        <span style={{ display: 'flex', alignItems: 'center' }}>
          {live && <span className="dot" />}
          <span>{title}</span>
        </span>
        <span className="right">
          <span className={`chip ${status === 'live' ? 'live' : status === 'idle' ? 'idle' : status}`}>
            {status}
          </span>
          {id && <span className="id">{id}</span>}
        </span>
      </header>
      <div className={`panel-bd${scroll ? ' scroll' : ''}`}>{children}</div>
    </section>
  )
}

// ── Current Task ──────────────────────────────────────────────────────────────
export function CurrentTask({ state, taskName, steps = [] }) {
  return (
    <Panel title="Current Task" id="ID/0x0A1" live>
      <div className="kv">
        <span className="k">Subject</span>
        <span className="v cyan glow">{taskName || '—'}</span>
      </div>
      <div className="kv">
        <span className="k">Routing</span>
        <span className="v">{state === 'thinking' ? 'Gemini 2.5 Pro' : 'Gemini 2.5 Flash'}</span>
      </div>
      <div className="kv">
        <span className="k">Mode</span>
        <span className="v">{state.toUpperCase()}</span>
      </div>
      <div className="hr" />
      <div className="k-label" style={{ marginBottom: 6 }}>Execution Trace</div>
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
        {steps.map((s, i) => (
          <li key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 11 }}>
            <span style={{
              color: s.done ? 'var(--hud-cyan-soft)' : s.active ? 'var(--hud-cyan)' : 'var(--hud-ink-faint)',
              width: 14
            }}>
              {s.done ? '▣' : s.active ? '▶' : '□'}
            </span>
            <span style={{
              flex: 1,
              color: s.done ? 'var(--hud-ink-dim)' : s.active ? 'var(--hud-cyan-soft)' : 'var(--hud-ink-faint)',
              textDecoration: s.done ? 'line-through solid rgba(34,211,238,.3)' : 'none'
            }}>
              {s.label}
            </span>
            {s.t && <span className="dim numeric" style={{ fontSize: 9 }}>{s.t}</span>}
          </li>
        ))}
      </ul>
    </Panel>
  )
}

// ── Active Subagents ──────────────────────────────────────────────────────────
const SUBAGENTS = [
  { key: 'math',     name: 'MathAgent',     desc: 'Calculus / linear algebra' },
  { key: 'writer',   name: 'WriterAgent',   desc: 'Prose / abstracts / latex' },
  { key: 'research', name: 'ResearchAgent', desc: 'Web · synthesis · citations' },
  { key: 'coder',    name: 'CoderAgent',    desc: 'Algorithms · scripts' },
  { key: 'shell',    name: 'Shell.run',     desc: 'PowerShell · system' },
  { key: 'vault',    name: 'Vault.write',   desc: 'Notes · memory' },
]

export function SubagentsPanel({ active = [] }) {
  return (
    <Panel title="Active Subagents" id="ID/0x0B2" scroll>
      {SUBAGENTS.map(s => {
        const isActive = active.includes(s.key)
        return (
          <div key={s.key} className={`sa${isActive ? ' active' : ''}`}>
            <svg className="ico" viewBox="0 0 24 24" fill="none">
              <circle cx="12" cy="12" r="9"
                stroke={isActive ? 'var(--hud-cyan)' : 'var(--hud-line-dim)'} />
              <circle cx="12" cy="12" r="3"
                fill={isActive ? 'var(--hud-cyan)' : 'transparent'}
                stroke={isActive ? 'var(--hud-cyan)' : 'var(--hud-ink-dim)'} />
              <line x1="12" y1="3"  x2="12" y2="6"  stroke={isActive ? 'var(--hud-cyan)' : 'var(--hud-line-dim)'} />
              <line x1="12" y1="18" x2="12" y2="21" stroke={isActive ? 'var(--hud-cyan)' : 'var(--hud-line-dim)'} />
              <line x1="3"  y1="12" x2="6"  y2="12" stroke={isActive ? 'var(--hud-cyan)' : 'var(--hud-line-dim)'} />
              <line x1="18" y1="12" x2="21" y2="12" stroke={isActive ? 'var(--hud-cyan)' : 'var(--hud-line-dim)'} />
            </svg>
            <span className="name">
              <b>{s.name}</b>
              <span className="desc">{s.desc}</span>
            </span>
            <span className="stat-chip">
              {isActive
                ? <><span className="pulse" style={{ display: 'inline-block', marginRight: 4, verticalAlign: 'middle' }} />running</>
                : 'idle'}
            </span>
          </div>
        )
      })}
    </Panel>
  )
}

// ── System Metrics ─────────────────────────────────────────────────────────────
function MeterRow({ label, value, max = 100, sub, danger }) {
  const segs = 18
  const lit  = Math.round((value / max) * segs)
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
        <span className="k-label">{label}</span>
        <span className="numeric cyan" style={{ fontSize: 10 }}>{Math.round(value)}{sub}</span>
      </div>
      <div className="meter">
        {Array.from({ length: segs }).map((_, i) => (
          <div key={i} className={`seg ${i < lit ? (danger && i >= segs - 2 ? 'alert' : i >= segs - 4 ? 'warn' : 'on') : ''}`} />
        ))}
      </div>
    </div>
  )
}

export function SystemMetrics({ cpu, gpu, ram, vram, mic, voice, model, latency }) {
  return (
    <Panel title="System Metrics" id="ID/0x0E5">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14 }}>
        <MeterRow label="CPU"          value={cpu}   sub="%" />
        <MeterRow label="GPU · RTX 4070" value={gpu}   sub="%" />
        <MeterRow label="RAM"          value={ram}   sub=" GB" max={32} />
        <MeterRow label="VRAM"         value={vram}  sub=" GB" max={12} danger />
        <MeterRow label="MIC LEVEL"    value={mic}   sub="%" />
        <MeterRow label="VOICE OUT"    value={voice} sub="%" />
      </div>
      <div className="hr" />
      <div className="kv"><span className="k">Active model</span><span className="v cyan">{model}</span></div>
      <div className="kv"><span className="k">Round-trip</span><span className="v">{latency} ms</span></div>
      <div className="kv"><span className="k">Network</span><span className="v">Tailscale · 100.84.12.7</span></div>
    </Panel>
  )
}

// ── Calendar ──────────────────────────────────────────────────────────────────
export function CalendarPanel({ today = '', events = [] }) {
  return (
    <Panel title="Schedule · Today" id="ID/0x0D4" scroll>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
        <span className="cyan glow" style={{ fontSize: 16, letterSpacing: '.18em' }}>{today}</span>
        <span className="dim" style={{ fontSize: 9, letterSpacing: '.22em', textTransform: 'uppercase' }}>
          {events.length} events
        </span>
      </div>
      <div className="hr" />
      <div className="cal">
        {events.map((e, i) => (
          <div key={i} className="row">
            <span className="t">{e.time}</span>
            <span className="ttl">{e.title}<span className="room"> · {e.where || ''}</span></span>
            <span className={`chip ${e.kind || 'live'}`}>{e.kind || 'next'}</span>
          </div>
        ))}
        {events.length === 0 && <span className="dim" style={{ fontSize: 10 }}>No events today</span>}
      </div>
    </Panel>
  )
}

// ── Project Tracker ───────────────────────────────────────────────────────────
export function ProjectTracker({ projects = [] }) {
  return (
    <Panel title="Project Tracker" id="ID/0x0C3" scroll>
      {projects.map((p, i) => (
        <div key={i} className="proj">
          <div className="top">
            <span className="ttl">{p.title}</span>
            <span className="due">Δ {p.due}</span>
          </div>
          <div className="pbar"><i style={{ width: `${p.progress}%` }} /></div>
          <div className="meta">
            <span>{p.progress}% complete</span>
            <span>·</span>
            <span>{p.stage}</span>
            <span>·</span>
            <span>{p.tag}</span>
          </div>
        </div>
      ))}
      {projects.length === 0 && <span className="dim" style={{ fontSize: 10 }}>No active projects</span>}
    </Panel>
  )
}

// ── Today's Progress ──────────────────────────────────────────────────────────
function RingProgress({ value, size = 80 }) {
  const r    = size / 2 - 6
  const c    = 2 * Math.PI * r
  const dash = c * (value / 100)
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} style={{ overflow: 'visible' }}>
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="var(--hud-line-dim)" strokeWidth="2" />
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="var(--hud-cyan)" strokeWidth="2"
        strokeDasharray={`${dash} ${c - dash}`} strokeDashoffset={c * 0.25}
        style={{ filter: 'drop-shadow(0 0 4px var(--hud-cyan))' }} />
      {Array.from({ length: 24 }).map((_, i) => {
        const a = (i / 24) * Math.PI * 2 - Math.PI / 2
        return <line key={i}
          x1={size/2 + Math.cos(a) * (r + 4)} y1={size/2 + Math.sin(a) * (r + 4)}
          x2={size/2 + Math.cos(a) * (r + 8)} y2={size/2 + Math.sin(a) * (r + 8)}
          stroke="var(--hud-line-dim)" strokeWidth="0.8" />
      })}
      <text x={size/2} y={size/2 + 1} textAnchor="middle" dominantBaseline="middle"
        fontSize="18" fill="var(--hud-cyan-soft)"
        style={{ filter: 'drop-shadow(0 0 4px var(--hud-cyan))', fontFamily: 'Share Tech Mono' }}>
        {value}%
      </text>
      <text x={size/2} y={size/2 + 14} textAnchor="middle" fontSize="7" fill="var(--hud-ink-dim)"
        style={{ letterSpacing: '.22em' }}>COMPLETE</text>
    </svg>
  )
}

export function ProgressToday({ jobsDone = 0, jobsTotal = 0, runtime = '00:00:00', tokensIn = 0, tokensOut = 0, cloudSpend = '0.00', costSaved = '0.00' }) {
  const pct = jobsTotal > 0 ? Math.round((jobsDone / jobsTotal) * 100) : 0
  return (
    <Panel title="Today · Progress" id="ID/0x0I9">
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <RingProgress value={pct} size={90} />
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, flex: 1 }}>
          <div className="stat"><span className="v">{jobsDone}/{jobsTotal}</span><span className="l">jobs done</span></div>
          <div className="stat"><span className="v">{runtime}</span><span className="l">runtime</span></div>
          <div className="stat"><span className="v">{(tokensIn / 1000).toFixed(1)}k</span><span className="l">tokens in</span></div>
          <div className="stat"><span className="v">{(tokensOut / 1000).toFixed(1)}k</span><span className="l">tokens out</span></div>
        </div>
      </div>
      <div className="hr" />
      <div className="kv"><span className="k">Cloud spend (today)</span><span className="v cyan">${cloudSpend}</span></div>
      <div className="kv"><span className="k">Cost saved (local)</span><span className="v">${costSaved}</span></div>
    </Panel>
  )
}

// ── Vault / Memory ─────────────────────────────────────────────────────────────
export function VaultPanel({ entries = [], chromaCount = 0 }) {
  return (
    <Panel title="Memory · Vault" id="ID/0x0F6" scroll>
      <div className="kv">
        <span className="k">ChromaDB vectors</span>
        <span className="v cyan numeric">{chromaCount.toLocaleString()}</span>
      </div>
      <div className="kv">
        <span className="k">Vault</span>
        <span className="v">notes / conversations / reports</span>
      </div>
      <div className="hr" />
      <div className="k-label" style={{ marginBottom: 6 }}>Recent retrievals</div>
      {entries.map((e, i) => (
        <div key={i} className="vault-entry">
          <span className="vt">{e.title}</span>
          <span className="vtag">{e.tag}</span>
          <span className="vts">{e.ts}</span>
        </div>
      ))}
      {entries.length === 0 && <span className="dim" style={{ fontSize: 10 }}>No recent retrievals</span>}
    </Panel>
  )
}

// ── Activity Feed ──────────────────────────────────────────────────────────────
export function ActivityFeed({ lines = [] }) {
  const feedRef = useRef(null)
  useEffect(() => { if (feedRef.current) feedRef.current.scrollTop = 0 }, [lines])
  return (
    <Panel title="Telemetry · Live" id="ID/0x0G7" scroll>
      <div className="feed" ref={feedRef}>
        {lines.slice().reverse().map(l => (
          <div key={l.id} className="line">
            <span className="ts">{l.ts}</span>
            <span className={`tag ${l.kind}`}>{l.kind}</span>
            <span className="body">{l.body}</span>
          </div>
        ))}
      </div>
    </Panel>
  )
}

// ── Conversation Transcript ───────────────────────────────────────────────────
export function Transcript({ turns = [], typing = false }) {
  const endRef = useRef(null)
  useEffect(() => { if (endRef.current) endRef.current.scrollIntoView({ behavior: 'smooth' }) }, [turns])
  return (
    <Panel title="Conversation" id="ID/0x0H8" scroll>
      <div className="xcript">
        {turns.map((t, i) => (
          <div key={i} className="turn">
            <span className={`who${t.who === 'j' ? ' j' : ''}`}>{t.who === 'j' ? 'J.A.R.V.I.S' : 'USER'}</span>
            <span className={`msg${t.who === 'j' ? ' j' : ''}`}>
              {t.text}
              {i === turns.length - 1 && typing && <span className="caret" />}
            </span>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </Panel>
  )
}

// ── Top Bar ───────────────────────────────────────────────────────────────────
export function TopBar({ state, clock, onClose }) {
  const stateText = {
    idle:      'STANDBY',
    listening: 'LISTENING',
    speaking:  'RESPONDING',
    thinking:  'REASONING · CLOUD',
    working:   'EXECUTING TASK',
  }[state] || 'STANDBY'

  return (
    <div className="bar top slot-top" style={{ WebkitAppRegion: 'drag' }}>
      <span className="stark-mark">STARK INDUSTRIES</span>
      <span className="dim faint">/// JARVIS · MARK XLII INTERFACE</span>
      <span className="sep" />
      <span className="dim">SESSION</span>
      <span className="cyan numeric">0x{clock.session}</span>
      <span className="sep" />
      <span className="dim">STATE</span>
      <span className="cyan glow">{stateText}</span>
      <span className="grow" />
      <span className="dim">{clock.date}</span>
      <span className="sep" />
      <span className="cyan glow numeric" style={{ fontSize: 14, letterSpacing: '.22em' }}>{clock.time}</span>
      <span className="sep" />
      <span className="dim">{clock.tz}</span>
      <span className="sep" />
      {/* Window controls (frameless) */}
      <span style={{ WebkitAppRegion: 'no-drag', display: 'flex', gap: 6, marginLeft: 8 }}>
        <button onClick={() => window.jarvis?.hideHud()}
          style={{ background: 'rgba(239,68,68,.7)', border: 'none', borderRadius: '50%',
            width: 12, height: 12, cursor: 'pointer' }} />
      </span>
    </div>
  )
}

// ── Bottom Bar ────────────────────────────────────────────────────────────────
export function BottomBar({ state, micLevel, latency, vaultCount = 0, uptime = '00:00:00' }) {
  return (
    <div className="bar bot slot-bot">
      <span className="dim">VOICE I/O</span>
      <span className={state === 'listening' ? 'cyan glow' : 'dim'}>● MIC</span>
      <span className={state === 'speaking'  ? 'cyan glow' : 'dim'}>● TTS</span>
      <div style={{ width: 120, display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="dim" style={{ fontSize: 9 }}>LEVEL</span>
        <div className="meter" style={{ flex: 1 }}>
          {Array.from({ length: 18 }).map((_, i) => (
            <div key={i} className={`seg${i < Math.round(micLevel * 18) ? ' on' : ''}`} />
          ))}
        </div>
      </div>
      <span className="sep" />
      <span className="dim">RTT</span><span className="cyan numeric">{latency}ms</span>
      <span className="sep" />
      <span className="dim">VAULT</span><span className="cyan">{vaultCount.toLocaleString()} vectors</span>
      <span className="sep" />
      <span className="dim">UPTIME</span><span className="cyan numeric">{uptime}</span>
      <span className="grow" />
      <span className="dim">↑/↓ MODULES   ⌥ SPACE PUSH-TO-TALK   ESC DISMISS</span>
    </div>
  )
}

// ── Center stage caption ──────────────────────────────────────────────────────
export function CenterCaption({ state, name }) {
  const lines = {
    idle:      ['AT YOUR SERVICE.', 'ALL SYSTEMS NOMINAL.'],
    listening: ['LISTENING…', 'AWAITING INSTRUCTION'],
    speaking:  ['SPEAKING', 'VOICE SYNTH · EDGE-TTS'],
    thinking:  ['REASONING', 'GEMINI 2.5 PRO · CLOUD ROUTE'],
    working:   ['EXECUTING', name || 'RUNNING TASK'],
  }[state] || ['STANDBY', '']
  return (
    <>
      <div className="voice-line">{lines[0]}</div>
      <div className="sub-line">{lines[1]}</div>
    </>
  )
}
