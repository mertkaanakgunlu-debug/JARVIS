/**
 * App.jsx — Full JARVIS HUD.
 * Connects to FastAPI WebSocket, uses fake-data fallback when offline.
 * Accent color changes automatically based on JARVIS state:
 *   idle/listening → cyan (#22d3ee)
 *   thinking/working → yellow (#FFC857)
 *   speaking → red (#FF5577)
 */
import React, { useState, useEffect, useRef, useCallback } from 'react'
import './styles.css'
import JarvisOrb, { OrbitalRings, VoiceBars } from './components/JarvisOrb'
import {
  TopBar, BottomBar, CenterCaption,
  CurrentTask, SubagentsPanel, SystemMetrics,
  CalendarPanel, ProgressToday, ProjectTracker,
  ActivityFeed, VaultPanel, Transcript,
} from './components/HudPanels'
import useJarvisSocket from './hooks/useJarvisSocket'
import useRemoteAudioSession from './hooks/useRemoteAudioSession'
import useClock from './hooks/useClock'
// Every invented value the HUD can show comes from this one module, and only
// on the disconnected branch. See its header and
// tests/test_no_synthetic_live_data.py.
import {
  useFakeMic, useFakeFeed, useFakeMetrics,
  PLACEHOLDER_EVENTS, PLACEHOLDER_PROJECTS, PLACEHOLDER_VAULT_ENTRIES,
  TASK_BY_STATE, TRANSCRIPT_BY_STATE,
} from './hooks/useFakeData'
import { readChatSse } from './lib/chatStream'
import { NO_VALUE } from './lib/display'

// Stable per-install conversation id (Faz 5 removed the server's silent
// auto-resume guess; the API has accepted an explicit conversation_id since
// the 12th session — the HUD just never sent one, so every server restart
// orphaned its conversation).
const CONVERSATION_ID_KEY = 'jarvis.conversation_id'
function loadConversationId() {
  try {
    let v = localStorage.getItem(CONVERSATION_ID_KEY)
    if (!v) {
      v = (crypto.randomUUID ? crypto.randomUUID() : `hud-${Date.now()}-${Math.random().toString(16).slice(2)}`)
      localStorage.setItem(CONVERSATION_ID_KEY, v)
    }
    return v
  } catch {
    return '' // storage unavailable — server falls back to a fresh session
  }
}

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

// ── Drop overlay ──────────────────────────────────────────────────────────────
function DropOverlay({ file, query, onQueryChange, onSend, onDismiss }) {
  const ext = file ? file.split('.').pop().toUpperCase() : ''
  const name = file ? file.split(/[\\/]/).pop() : ''
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9999,
      background: 'rgba(0,0,0,.82)',
      display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: 16,
    }}>
      <div style={{
        border: '1px solid var(--hud-cyan)', borderRadius: 12,
        padding: '28px 32px', maxWidth: 520, width: '90%',
        background: 'rgba(0,8,16,.96)',
        boxShadow: 'var(--hud-glow)',
      }}>
        <div style={{ fontSize: 9, letterSpacing: '.22em', color: 'var(--hud-cyan)', marginBottom: 12 }}>
          // FILE ATTACHED
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 18 }}>
          <span style={{
            fontSize: 9, padding: '2px 7px', borderRadius: 4,
            border: '1px solid var(--hud-cyan)', color: 'var(--hud-cyan)',
            letterSpacing: '.14em',
          }}>{ext}</span>
          <span style={{ color: 'var(--hud-cyan-soft)', fontSize: 12, wordBreak: 'break-all' }}>{name}</span>
        </div>
        <input
          autoFocus
          value={query}
          onChange={e => onQueryChange(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') onSend(); if (e.key === 'Escape') onDismiss(); }}
          placeholder="Dosya hakkında bir soru sor, ya da Enter ile analiz başlat…"
          style={{
            width: '100%', background: 'rgba(255,255,255,.04)',
            border: '1px solid var(--hud-line)', borderRadius: 8,
            color: 'var(--hud-cyan-soft)', fontSize: 12,
            padding: '8px 12px', outline: 'none', boxSizing: 'border-box',
            marginBottom: 14, letterSpacing: '.03em',
          }}
        />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button onClick={onDismiss} style={{
            background: 'transparent', border: '1px solid var(--hud-line-dim)',
            color: 'var(--hud-line)', borderRadius: 6, padding: '5px 14px',
            cursor: 'pointer', fontSize: 10, letterSpacing: '.1em',
          }}>DISMISS</button>
          <button onClick={onSend} style={{
            background: 'var(--hud-cyan)', border: 'none',
            color: '#000', borderRadius: 6, padding: '5px 18px',
            cursor: 'pointer', fontSize: 10, letterSpacing: '.1em', fontWeight: 700,
          }}>ANALYZE</button>
        </div>
      </div>
    </div>
  )
}

// ── Drop response panel (streaming SSE result) ────────────────────────────────
function DropResponseOverlay({ text, done, preparing, onDismiss }) {
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }) }, [text])
  return (
    <div style={{
      position: 'fixed', bottom: 80, left: '50%', transform: 'translateX(-50%)', zIndex: 9997,
      width: 480, maxHeight: 340,
      background: 'rgba(0,8,16,.97)',
      border: '1px solid var(--hud-cyan)',
      borderRadius: 12,
      boxShadow: 'var(--hud-glow)',
      display: 'flex', flexDirection: 'column',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '8px 14px', borderBottom: '1px solid var(--hud-line-dim)',
      }}>
        <span style={{ fontSize: 9, letterSpacing: '.22em', color: 'var(--hud-cyan)' }}>
          {done ? '// JARVIS' : preparing ? '// JARVIS · PREPARING OUTPUT…' : '// JARVIS · ANALYZING…'}
        </span>
        {done && (
          <button onClick={onDismiss} style={{
            background: 'transparent', border: 'none',
            color: 'var(--hud-line)', cursor: 'pointer', fontSize: 14, lineHeight: 1,
          }}>×</button>
        )}
      </div>
      <div style={{
        padding: '10px 14px', overflowY: 'auto', flex: 1,
        fontSize: 11, color: 'var(--hud-cyan-soft)',
        lineHeight: 1.6, letterSpacing: '.02em', whiteSpace: 'pre-wrap',
      }}>
        {text || <span style={{ color: 'var(--hud-line)', fontStyle: 'italic' }}>…</span>}
        <div ref={endRef} />
      </div>
    </div>
  )
}

// ── L3 confirmation overlay (Faz 4 gate — approve/deny round-trip) ────────────
// Renders the structured confirmation_required payload: one row per pending
// tool call, using policy_guard.describe_call()'s human-readable description.
// Fail-closed by design: there is no "dismiss" — Escape counts as DENY, and
// an optional reason can be attached to a deny (server accepts
// "deny:<reason>", same vocabulary as the CLI prompt).
function ConfirmationOverlay({ payload, busy, onDecision }) {
  const [reason, setReason] = useState('')
  const tools = payload?.tools || []
  useEffect(() => {
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); onDecision('deny') }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onDecision])
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 10000,
      background: 'rgba(0,0,0,.82)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <div style={{
        border: '1px solid var(--hud-amber, #FFC857)', borderRadius: 12,
        padding: '24px 28px', maxWidth: 560, width: '92%',
        background: 'rgba(16,8,0,.97)',
        boxShadow: '0 0 24px rgba(255,200,87,.25)',
      }}>
        <div style={{ fontSize: 9, letterSpacing: '.22em', color: '#FFC857', marginBottom: 14 }}>
          // ONAY GEREKİYOR — L3 İŞLEM {tools.length > 1 ? `(${tools.length} çağrı)` : ''}
        </div>
        {tools.map((t, i) => (
          <div key={t.id || i} style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
              <span style={{
                fontSize: 9, padding: '2px 7px', borderRadius: 4,
                border: '1px solid #FFC857', color: '#FFC857', letterSpacing: '.12em',
              }}>{(t.name || '?').toUpperCase()}</span>
            </div>
            <div style={{ color: 'var(--hud-cyan-soft)', fontSize: 12, lineHeight: 1.5 }}>
              {t.description || JSON.stringify(t.args || {})}
            </div>
          </div>
        ))}
        <input
          value={reason}
          onChange={e => setReason(e.target.value)}
          placeholder="Reddedersen gerekçe (opsiyonel) — modele iletilir"
          disabled={busy}
          style={{
            width: '100%', boxSizing: 'border-box',
            background: 'rgba(255,255,255,.04)',
            border: '1px solid var(--hud-line-dim)', borderRadius: 8,
            color: 'var(--hud-cyan-soft)', fontSize: 11,
            padding: '7px 10px', outline: 'none', margin: '6px 0 14px',
          }}
        />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button
            onClick={() => onDecision(reason.trim() ? `deny:${reason.trim()}` : 'deny')}
            disabled={busy}
            style={{
              background: 'rgba(239,68,68,.85)', border: 'none', color: '#fff',
              borderRadius: 6, padding: '6px 18px', cursor: busy ? 'wait' : 'pointer',
              fontSize: 10, letterSpacing: '.12em', fontWeight: 700, opacity: busy ? 0.5 : 1,
            }}>DENY</button>
          <button
            onClick={() => onDecision('approve')}
            disabled={busy}
            style={{
              background: 'var(--hud-cyan)', border: 'none', color: '#000',
              borderRadius: 6, padding: '6px 18px', cursor: busy ? 'wait' : 'pointer',
              fontSize: 10, letterSpacing: '.12em', fontWeight: 700, opacity: busy ? 0.5 : 1,
            }}>APPROVE</button>
        </div>
      </div>
    </div>
  )
}

// ── Drag-hint overlay (while file is hovering) ────────────────────────────────
function DragHint() {
  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 9998,
      border: '2px dashed var(--hud-cyan)',
      background: 'rgba(0,20,32,.65)',
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      pointerEvents: 'none',
    }}>
      <span style={{ fontSize: 11, letterSpacing: '.3em', color: 'var(--hud-cyan)' }}>
        DROP FILE TO ANALYZE
      </span>
    </div>
  )
}

export default function App() {
  const [apiUrl, setApiUrl] = useState(null)
  const [accent, setAccent]   = useState(STATE_ACCENT.idle)
  const [dragging, setDragging] = useState(false)
  const [dropFile, setDropFile] = useState(null)     // native path string
  const [dropQuery, setDropQuery] = useState('')
  const [dropResponse, setDropResponse] = useState(null) // {text, done}
  const dragCounter = useRef(0)

  // Panel visibility — all 9 panels, independently togglable
  const ALL_PANEL_KEYS = ['task','subagents','metrics','schedule','progress','projects','telemetry','memory','conversation']
  const [panelVis, setPanelVis] = useState(
    Object.fromEntries(ALL_PANEL_KEYS.map(k => [k, true]))
  )
  const togglePanel = useCallback((key) => {
    setPanelVis(prev => ({ ...prev, [key]: !prev[key] }))
  }, [])
  const setAllPanels = useCallback((visible) => {
    setPanelVis(() => Object.fromEntries(ALL_PANEL_KEYS.map(k => [k, visible])))
  }, [])
  const handlePanelControl = useCallback((action, panels) => {
    const targets = panels === 'all' ? ALL_PANEL_KEYS : (Array.isArray(panels) ? panels : [panels])
    setPanelVis(prev => {
      const next = { ...prev }
      for (const p of targets) {
        if (!(p in next)) continue
        if (action === 'show')   next[p] = true
        else if (action === 'hide')   next[p] = false
        else if (action === 'toggle') next[p] = !prev[p]
      }
      return next
    })
  }, [])

  // Local chat messages (typed via ChatBar); merged with WS transcript for display
  const [localChat, setLocalChat] = useState([])
  const [chatBusy, setChatBusy]   = useState(false)
  const addLocalMessage = useCallback((msg) => {
    setLocalChat(prev => [...prev.slice(-40), msg])
  }, [])

  // Get API URL/key from Electron main process
  const [apiKey, setApiKey] = useState('')
  useEffect(() => {
    window.jarvis?.onConfig(cfg => { setApiUrl(cfg.apiUrl); setApiKey(cfg.apiKey || '') })
    // Fallback for browser dev mode
    if (!window.jarvis) setApiUrl('http://127.0.0.1:8000')
    return () => window.jarvis?.removeAllListeners('config')
  }, [])

  // ── L3 confirmation state (Faz 4 gate, HUD leg) ────────────────────────────
  // {id, payload} of the pending approval. Fed from BOTH sources: the HUD's
  // own /chat* SSE streams (structured confirmation_required frame) and the
  // WS broadcast (confirmations initiated on ANY transport — voice, another
  // client). Same id may arrive from both for a HUD-initiated turn;
  // overwriting with an identical object is harmless, so no dedup machinery.
  const [pendingConfirmation, setPendingConfirmation] = useState(null)
  const [confirmBusy, setConfirmBusy] = useState(false)
  const conversationId = useRef(loadConversationId()).current

  const handleConfirmationRequired = useCallback((conf) => {
    if (!conf || !conf.id) return
    setPendingConfirmation(prev => (prev && prev.id === conf.id ? prev : conf))
  }, [])

  const resolveConfirmation = useCallback(async (decision) => {
    const conf = pendingConfirmation
    if (!conf || confirmBusy) return
    setConfirmBusy(true)
    try {
      const resp = await fetch(`${apiUrl}/chat/confirm/${encodeURIComponent(conf.id)}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(apiKey ? { 'X-API-Key': apiKey } : {}),
        },
        body: JSON.stringify({ decision }),
      })
      const out = await readChatSse(resp)
      if (out.text) addLocalMessage({ who: 'j', text: out.text })
      if (out.error) addLocalMessage({ who: 'j', text: '⚠ ' + out.error })
      // out.text above is already the corrected/authoritative answer even
      // when a progress or final_answer frame arrived mid-stream (readChatSse
      // resolves that, not this call site) — nothing else to do with either
      // frame kind here; this path only ever reads the final out.text, never
      // renders incrementally.
      if (out.confirmation) {
        // A SECOND same-turn interrupt (the exact case the backend's
        // resume_and_stream re-emits the structured frame for) — swap the
        // prompt in place and stay busy.
        setPendingConfirmation(out.confirmation)
        return
      }
      setPendingConfirmation(null)
      setChatBusy(false)
    } catch (e) {
      addLocalMessage({ who: 'j', text: `⚠ Onay iletilemedi: ${e.message}` })
      // Leave the prompt up — the pending confirmation may still be alive
      // server-side (TTL-bound); the user can retry or deny.
    } finally {
      setConfirmBusy(false)
    }
  }, [pendingConfirmation, confirmBusy, apiUrl, apiKey, addLocalMessage])

  // Faz 3: Electron itself as the mic/speaker for a JARVIS conversation, using
  // the server's Whisper/Piper (see docs/VOICE_PROTOCOL.md). Independent of
  // the local wakeword/PTT loop -- /ws's audio_session_start automatically
  // pauses that loop for the duration of this session. Declared before
  // useJarvisSocket() because that hook's onAudioChunk/onAudioControl options
  // need remoteAudio's handlers; sendRaw (which remoteAudio needs) flows the
  // other way, passed into start()/stop() at call time instead.
  const remoteAudio = useRemoteAudioSession()

  // Live data from WebSocket
  const {
    connected, state, transcript, feedLines, task, metrics, calEvents, vaultData, progress, todos,
    micLevel: wsMicLevel, modelStatus, sendRaw,
  } = useJarvisSocket(apiUrl, apiKey, {
    onPanelControl: handlePanelControl,
    onAudioChunk: (buf) => remoteAudio.handleAudioChunk(buf),
    onAudioControl: (msg) => remoteAudio.handleAudioControl(msg),
    onConfirmation: handleConfirmationRequired,
  })

  // Space → toggle the remote-audio session (first press starts capture +
  // begins streaming mic audio to the backend; second press stops it).
  // (ignored when a text input / textarea is focused so ChatBar still works)
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (e.code !== 'Space' || e.altKey || e.ctrlKey || e.metaKey) return
      const tag = document.activeElement?.tagName?.toLowerCase()
      if (tag === 'input' || tag === 'textarea') return
      e.preventDefault()
      if (remoteAudio.active) remoteAudio.stop(sendRaw)
      else remoteAudio.start(sendRaw)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [remoteAudio, sendRaw])

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

  // ── Drag-and-drop handlers ───────────────────────────────────────────────────
  const handleDragEnter = useCallback(e => {
    e.preventDefault()
    dragCounter.current++
    if (e.dataTransfer.types.includes('Files')) setDragging(true)
  }, [])
  const handleDragLeave = useCallback(e => {
    e.preventDefault()
    dragCounter.current--
    if (dragCounter.current === 0) setDragging(false)
  }, [])
  const handleDragOver = useCallback(e => { e.preventDefault() }, [])
  const handleDrop = useCallback(e => {
    e.preventDefault()
    dragCounter.current = 0
    setDragging(false)
    const f = e.dataTransfer.files[0]
    if (!f) return
    setDropFile(f)
    setDropQuery('')
  }, [])

  const sendDroppedFile = useCallback(async () => {
    if (!dropFile || !apiUrl) return

    const formData = new FormData()
    formData.append('file', dropFile)
    formData.append('query', dropQuery.trim())
    formData.append('language', 'tr')
    if (conversationId) formData.append('conversation_id', conversationId)

    setDropFile(null)
    setDropQuery('')
    setDropResponse({ text: '', done: false, preparing: false })

    try {
      const resp = await fetch(`${apiUrl}/chat/upload`, {
        method: 'POST',
        headers: apiKey ? { 'X-API-Key': apiKey } : undefined,
        body: formData,
      })
      const out = await readChatSse(resp, {
        onToken: (token) => setDropResponse(r => ({ ...r, text: (r?.text || '') + token, preparing: false })),
        // Completion-contract TTFB: this is the one HUD surface that renders
        // tokens incrementally (onToken above), so it is also the one where
        // the pre-fix final_answer bug was reachable live — a correction
        // arriving after some draft had already streamed appended raw JSON
        // onto the visible text instead of replacing it. onFinal now REPLACES
        // rather than appends, matching readChatSse's own out.text contract.
        onProgress: () => setDropResponse(r => ({ ...r, preparing: true })),
        onFinal: (text) => setDropResponse(r => ({ ...r, text, preparing: false })),
      })
      if (out.error) { setDropResponse(r => ({ text: (r?.text || '') + out.error, done: true, preparing: false })); return }
      if (out.confirmation) {
        // An uploaded-file query can hit the L3 gate too ("read this and
        // email it"). Same overlay; the continuation flows into the
        // transcript, so close the drop panel with a pointer.
        setDropResponse(r => ({ text: (r?.text || '') + '\n— onay bekleniyor (prompt açıldı) —', done: true }))
        handleConfirmationRequired(out.confirmation)
        return
      }
      setDropResponse(r => ({ ...r, done: true }))
    } catch (e) {
      setDropResponse({ text: `Hata: ${e.message}`, done: true })
    }
  }, [dropFile, dropQuery, apiUrl, apiKey, conversationId, handleConfirmationRequired])

  // ── LIVE DATA INTEGRITY INVARIANT (2026-07-31) ─────────────────────────────
  // While `connected` is true NO field may show a synthetic, placeholder,
  // state-derived or random value. Absent data renders as "—"/"veri yok"; the
  // demo data below is reachable ONLY when disconnected, where the badge
  // already reads "○ OFFLINE · DEMO DATA".
  //
  // Every `connected && X.length ? X : fake` below used to be exactly that:
  // truthy-length guards that silently swapped in demo content whenever a real
  // stream happened to be empty. So a live HUD showed an animated mic meter
  // with no voice session, an activity feed of tool calls that never ran
  // ("Gemini 2.5 Pro escalation", "report.compile → report.pdf · 312 KB"), a
  // vault count of 2847, and invented calendar entries — all under a LIVE badge.
  // The owner had no way to tell which panels were real.
  const fakeMic      = useFakeMic(state)
  const fakeFeed     = useFakeFeed(state)
  const fakeMetrics  = useFakeMetrics(state)

  // Faz 3: real (not simulated) level once a voice session (local or remote) is
  // actively pushing mic_level events. The *5 scale is a starting heuristic
  // (ambient room noise measured ~0.00002 RMS, speech ~0.14 during
  // verification) -- recalibrate once seen live.
  const realMicLevel = wsMicLevel != null ? Math.min(1, wsMicLevel.rms * 5) : null
  // null (not 0) when connected with no voice session: "not measured" is not
  // "silent". Voice is parked, so this is the normal state today.
  //
  // The ORB still animates on null, and that is intended, not an oversight:
  // JarvisOrb falls through to its state-driven amplitude (null > 0 is false),
  // which is decoration — a breathing sphere asserts nothing about the
  // microphone. The invariant governs READOUTS, and the numeric one beside it
  // (micPct) correctly reads "—". Freezing the orb would cost the HUD its life
  // without making anything more honest.
  const micLevel = connected ? realMicLevel : fakeMic
  const micPct   = micLevel == null ? null : Math.round(micLevel * 100)
  const feed     = connected ? feedLines : fakeFeed
  const met      = connected ? metrics : fakeMetrics

  const clock  = useClock()
  const uptime = useUptime()

  // Nothing reports which sub-agents are actually running, so when connected
  // this is empty rather than guessed. It used to be derived from the animation
  // state -- 'thinking' displayed 'math' and 'writer' as busy regardless of
  // what the turn was doing.
  const activeAgents = connected ? []
    : state === 'thinking' ? ['math', 'writer']
    : state === 'working'  ? ['coder', 'shell']
    : state === 'speaking' ? ['vault']
    : []

  const calendarEvents  = connected ? calEvents : PLACEHOLDER_EVENTS
  const projects        = connected
    ? todos.map(t => ({
        title: t.title,
        progress: Math.round((t.priority_score || 0) * 100),
        due: t.due || '—',
        stage: t.priority || 'low',
        tag: t.category || 'other',
      }))
    : PLACEHOLDER_PROJECTS
  const vaultEntries    = connected ? vaultData.entries : PLACEHOLDER_VAULT_ENTRIES
  const vaultCount      = connected ? vaultData.count : 2847
  const displayTask     = connected ? task : (TASK_BY_STATE[state] || TASK_BY_STATE.idle)
  // Merge WS voice messages + locally typed chat messages. When connected an
  // empty conversation stays empty -- the scripted TRANSCRIPT_BY_STATE demo
  // dialogue must never appear as if JARVIS had said it.
  const mergedMessages    = [...transcript, ...localChat]
  const displayTranscript = connected
    ? mergedMessages
    : (mergedMessages.length > 0 ? mergedMessages : (TRANSCRIPT_BY_STATE[state] || []))

  // Progress: use WS data or zeroes (no fake placeholders)
  const cloudSpend = progress.cloudSpend ?? '0.0000'
  const costSaved  = progress.costSaved  ?? '0.0000'

  return (
    <div
      style={{ position: 'relative', width: '100%', height: '100%' }}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
    >
      {dragging && <DragHint />}
      {pendingConfirmation && (
        <ConfirmationOverlay
          payload={pendingConfirmation.payload}
          busy={confirmBusy}
          onDecision={resolveConfirmation}
        />
      )}
      {dropFile && (
        <DropOverlay
          file={dropFile.name ?? dropFile}
          query={dropQuery}
          onQueryChange={setDropQuery}
          onSend={sendDroppedFile}
          onDismiss={() => { setDropFile(null); setDropQuery('') }}
        />
      )}
      {dropResponse && (
        <DropResponseOverlay
          text={dropResponse.text}
          done={dropResponse.done}
          preparing={dropResponse.preparing}
          onDismiss={() => setDropResponse(null)}
        />
      )}
      <div className="hud-grid" />
      <div className="hud-scan" />

      <div className="hud-stage" data-density="comfy" data-layout="default">

        {/* Top bar */}
        <TopBar state={state} clock={clock} panelVis={panelVis} onTogglePanel={togglePanel} onSetAllPanels={setAllPanels} />

        {/* Left column */}
        <div className="slot-l1" style={{ display: 'flex', minHeight: 0 }}>
          {panelVis.task && <CurrentTask state={state} taskName={displayTask.name} steps={displayTask.steps} modelStatus={modelStatus} />}
        </div>
        <div className="slot-l2" style={{ display: 'flex', minHeight: 0 }}>
          {panelVis.subagents && <SubagentsPanel active={activeAgents} />}
        </div>
        <div className="slot-l3" style={{ display: 'flex', minHeight: 0 }}>
          {panelVis.metrics && <SystemMetrics
            cpu={met.cpu} gpu={met.gpu} ram={met.ram} vram={met.vram}
            mic={micPct}
            voice={state === 'speaking' ? micPct : null}
            modelStatus={modelStatus}
            latency={modelStatus.latency_ms ?? met.latency}
          />}
        </div>

        {/* Center — orb */}
        <div className="slot-c center-stage">
          <div style={{ position: 'relative', width: 560, height: 560,
            display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <OrbitalRings size={560} accent={accent} />
            <JarvisOrb size={420} state={state} accent={accent} micLevel={micLevel} />
          </div>
          <CenterCaption state={state} name={displayTask.name} modelStatus={modelStatus} />
          <div style={{ display: 'flex', gap: 18, alignItems: 'center', marginTop: 4 }}>
            <span className="dim" style={{ fontSize: 9, letterSpacing: '.22em' }}>VOICE I/O</span>
            <VoiceBars state={state} accent={accent} count={28} />
            <span className="dim numeric" style={{ fontSize: 10 }}>
              {micPct == null ? NO_VALUE : `${micPct}%`}
            </span>
          </div>
          {/* Connection status. OFFLINE names the demo data explicitly: every
              placeholder in this file is reachable only on this branch, and the
              badge is the reader's one signal that what they see is invented. */}
          <div style={{ position: 'absolute', bottom: 8, right: 8, fontSize: 9,
            color: connected ? 'var(--hud-cyan)' : 'var(--hud-amber)',
            letterSpacing: '.14em', textTransform: 'uppercase' }}>
            {connected ? '● LIVE' : '○ OFFLINE · DEMO DATA'}
          </div>
        </div>

        {/* Right column */}
        <div className="slot-r1" style={{ display: 'flex', minHeight: 0 }}>
          {panelVis.schedule && <CalendarPanel today={clock.date} events={calendarEvents} />}
        </div>
        <div className="slot-r2" style={{ display: 'flex', minHeight: 0 }}>
          {panelVis.progress && <ProgressToday
            jobsDone={progress.jobsDone ?? 0} jobsTotal={progress.jobsTotal || 1}
            runtime={progress.runtime || uptime}
            tokensIn={progress.tokensIn ?? 0} tokensOut={progress.tokensOut ?? 0}
            cloudSpend={cloudSpend} costSaved={costSaved}
          />}
        </div>
        <div className="slot-r3" style={{ display: 'flex', minHeight: 0 }}>
          {panelVis.projects && <ProjectTracker projects={projects} live={connected && todos.length > 0} />}
        </div>

        {/* Center-bottom strip */}
        <div className="slot-cb center-bottom-strip">
          <div style={{ display: 'flex', minHeight: 0 }}>
            {panelVis.telemetry && <ActivityFeed lines={feed} />}
          </div>
          <div style={{ display: 'flex', minHeight: 0 }}>
            {panelVis.memory && <VaultPanel entries={vaultEntries} chromaCount={vaultCount} />}
          </div>
          <div style={{ display: 'flex', minHeight: 0 }}>
            {panelVis.conversation && <Transcript turns={displayTranscript} typing={state === 'speaking' || state === 'listening'} />}
          </div>
        </div>

        <BottomBar
          state={state} micLevel={micLevel} latency={met.latency}
          vaultCount={vaultCount} uptime={uptime}
          apiUrl={apiUrl} apiKey={apiKey}
          conversationId={conversationId}
          onMessage={addLocalMessage}
          onConfirmation={handleConfirmationRequired}
          busy={chatBusy}
          onBusy={setChatBusy}
          onPickFile={f => { setDropFile(f); setDropQuery('') }}
        />
      </div>
    </div>
  )
}
