/**
 * useJarvisSocket — connects to the JARVIS FastAPI WebSocket endpoint
 * and returns live state + data.
 *
 * Protocol (server → client JSON messages):
 *   {type:"state",   value:"listening"|"speaking"|"thinking"|"working"|"idle"}
 *   {type:"message", who:"u"|"j", text:"..."}
 *   {type:"tool_call", kind:"tool"|"cloud"|"local"|"note", body:"..."}
 *   {type:"task",    name:"...", steps:[{label,done?,active?,t?}]}
 *   {type:"metrics", cpu, gpu, ram, vram, latency}
 *   {type:"calendar",events:[{time,title,where,kind}]}
 *   {type:"vault",   entries:[{title,tag,ts}], count:N}
 *   {type:"progress",jobsDone:N,jobsTotal:N,runtime:"...",tokensIn:N,tokensOut:N}
 */

import { useState, useEffect, useRef, useCallback } from 'react'

const RECONNECT_MS = 3000

function useJarvisSocket(apiUrl, options = {}) {
  const wsUrl = apiUrl ? apiUrl.replace(/^http/, 'ws') + '/ws' : null

  const [connected, setConnected]   = useState(false)
  const [state, setState]           = useState('idle')
  const [transcript, setTranscript] = useState([])
  const [feedLines, setFeedLines]   = useState([])
  const [task, setTask]             = useState({ name: '—', steps: [] })
  const [metrics, setMetrics]       = useState({ cpu: 28, gpu: 64, ram: 14.2, vram: 5.7, latency: 312 })
  const [calEvents, setCalEvents]   = useState([])
  const [vaultData, setVaultData]   = useState({ entries: [], count: 0 })
  const [progress, setProgress]     = useState({ jobsDone: 0, jobsTotal: 0, runtime: '00:00:00', tokensIn: 0, tokensOut: 0 })
  const [todos, setTodos]           = useState([])

  const wsRef           = useRef(null)
  const feedId          = useRef(0)
  const timerRef        = useRef(null)
  const onPanelCtrlRef  = useRef(options.onPanelControl)
  useEffect(() => { onPanelCtrlRef.current = options.onPanelControl })

  const connect = useCallback(() => {
    if (!wsUrl) return
    if (wsRef.current && wsRef.current.readyState < 2) return // already open/connecting

    const ws = new WebSocket(wsUrl)
    wsRef.current = ws

    ws.onopen = () => {
      setConnected(true)
      if (timerRef.current) { clearTimeout(timerRef.current); timerRef.current = null }
    }

    ws.onclose = () => {
      setConnected(false)
      timerRef.current = setTimeout(connect, RECONNECT_MS)
    }

    ws.onerror = () => {
      ws.close()
    }

    ws.onmessage = (ev) => {
      let msg
      try { msg = JSON.parse(ev.data) } catch { return }

      switch (msg.type) {
        case 'state':
          setState(msg.value)
          // Relay to main process so it can show/hide widget
          window.jarvis?.sendState(msg.value)
          break

        case 'message':
          setTranscript(prev => [...prev.slice(-30), { who: msg.who, text: msg.text }])
          break

        case 'tool_call':
          setFeedLines(prev => [...prev, {
            id: feedId.current++,
            kind: msg.kind || 'tool',
            body: msg.body || '',
            ts: new Date().toLocaleTimeString('en-GB', { hour12: false })
          }].slice(-20))
          break

        case 'task':
          setTask({ name: msg.name || '—', steps: msg.steps || [] })
          break

        case 'metrics':
          setMetrics(m => ({ ...m, ...msg }))
          break

        case 'calendar':
          setCalEvents(msg.events || [])
          break

        case 'vault':
          setVaultData({ entries: msg.entries || [], count: msg.count || 0 })
          break

        case 'todos':
          setTodos(msg.items || [])
          break

        case 'show_hud':
          window.jarvis?.showHud()
          break

        case 'panel_control':
          onPanelCtrlRef.current?.(msg.action, msg.panels)
          break

        case 'progress':
          setProgress(p => ({ ...p, ...msg }))
          break

        default:
          break
      }
    }
  }, [wsUrl])

  useEffect(() => {
    connect()
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
      wsRef.current?.close()
    }
  }, [connect])

  return { connected, state, transcript, feedLines, task, metrics, calEvents, vaultData, progress, todos }
}

export default useJarvisSocket
