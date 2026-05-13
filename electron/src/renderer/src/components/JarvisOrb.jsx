/**
 * JarvisOrb — Ultron-vision style spherical particle mesh.
 * Canvas-based, state-reactive. Ported from the Claude Design prototype.
 */
import { useEffect, useRef, useMemo } from 'react'

// ── Fibonacci sphere points ────────────────────────────────────────────────────
function fibonacciSphere(n) {
  const pts = []
  const phi = Math.PI * (3 - Math.sqrt(5))
  for (let i = 0; i < n; i++) {
    const y = 1 - (i / (n - 1)) * 2
    const r = Math.sqrt(1 - y * y)
    const theta = phi * i
    pts.push([Math.cos(theta) * r, y, Math.sin(theta) * r])
  }
  return pts
}

// ── Lat/lon wireframe lines ────────────────────────────────────────────────────
function meshLines() {
  const lines = []
  for (let lat = -60; lat <= 60; lat += 30) {
    const phi = (lat * Math.PI) / 180
    const r = Math.cos(phi), y = Math.sin(phi)
    const ring = []
    for (let a = 0; a <= 360; a += 6) {
      const t = (a * Math.PI) / 180
      ring.push([Math.cos(t) * r, y, Math.sin(t) * r])
    }
    lines.push(ring)
  }
  for (let lon = 0; lon < 180; lon += 30) {
    const t = (lon * Math.PI) / 180
    const ring = []
    for (let a = 0; a <= 360; a += 6) {
      const p = (a * Math.PI) / 180
      ring.push([Math.cos(p) * Math.cos(t), Math.sin(p), Math.cos(p) * Math.sin(t)])
    }
    lines.push(ring)
  }
  return lines
}

function rotXYZ(p, rx, ry) {
  let [x, y, z] = p
  const cy = Math.cos(ry), sy = Math.sin(ry)
  ;[x, z] = [x * cy + z * sy, -x * sy + z * cy]
  const cx = Math.cos(rx), sx = Math.sin(rx)
  ;[y, z] = [y * cx - z * sx, y * sx + z * cx]
  return [x, y, z]
}

function hexToRgb(hex) {
  const h = hex.replace('#', '')
  const v = h.length === 3 ? h.split('').map(c => c + c).join('') : h
  return [parseInt(v.slice(0, 2), 16), parseInt(v.slice(2, 4), 16), parseInt(v.slice(4, 6), 16)]
}

// ── Main component ─────────────────────────────────────────────────────────────
export default function JarvisOrb({ size = 420, state = 'idle', accent = '#22d3ee', micLevel = 0 }) {
  const canvasRef  = useRef(null)
  const stateRef   = useRef(state)
  const accentRef  = useRef(accent)
  const micRef     = useRef(micLevel)

  useEffect(() => { stateRef.current = state },    [state])
  useEffect(() => { accentRef.current = accent },  [accent])
  useEffect(() => { micRef.current = micLevel },   [micLevel])

  const points = useMemo(() => fibonacciSphere(900), [])
  const lines  = useMemo(() => meshLines(),           [])

  useEffect(() => {
    const cv = canvasRef.current
    const ctx = cv.getContext('2d')
    const dpr = Math.max(1, window.devicePixelRatio || 1)
    cv.width = size * dpr; cv.height = size * dpr
    cv.style.width = size + 'px'; cv.style.height = size + 'px'
    ctx.scale(dpr, dpr)

    let raf, t0 = performance.now()

    function frame(now) {
      const t  = (now - t0) / 1000
      const st = stateRef.current
      const [r, g, b] = hexToRgb(accentRef.current)

      const speedMul = st === 'thinking' ? 1.8 : st === 'working' ? 1.2 : st === 'speaking' ? 0.9 : 0.45
      const rx = Math.sin(t * 0.35 * speedMul) * 0.5 + 0.2
      const ry = t * 0.45 * speedMul

      const amp = (() => {
        if (micRef.current > 0) return micRef.current
        if (st === 'speaking')  return 0.55 + 0.35 * (Math.sin(t * 8) * 0.5 + Math.sin(t * 17.3) * 0.35 + Math.sin(t * 31.1) * 0.15)
        if (st === 'listening') return 0.25 + 0.15 * Math.sin(t * 1.6)
        if (st === 'thinking')  return 0.45 + 0.2  * Math.sin(t * 4.2)
        if (st === 'working')   return 0.7  + 0.1  * Math.sin(t * 5.5)
        return 0.2 + 0.05 * Math.sin(t * 1.2)
      })()

      const baseR  = size * 0.36
      const radius = baseR * (1 + (amp - 0.3) * 0.08)

      ctx.clearRect(0, 0, size, size)
      const cx = size / 2, cy = size / 2

      // Background haze
      const hazeAlpha = 0.18 + amp * 0.14
      const hazeGrad  = ctx.createRadialGradient(cx, cy, baseR * 0.2, cx, cy, baseR * 1.6)
      hazeGrad.addColorStop(0,   `rgba(${r},${g},${b},${hazeAlpha})`)
      hazeGrad.addColorStop(0.6, `rgba(${r},${g},${b},${hazeAlpha * 0.3})`)
      hazeGrad.addColorStop(1,   `rgba(0,0,0,0)`)
      ctx.fillStyle = hazeGrad
      ctx.fillRect(0, 0, size, size)

      // Mesh wireframe (back then front)
      const drawMesh = (frontPass) => {
        for (const ring of lines) {
          ctx.beginPath()
          let first = true
          for (const p of ring) {
            const [x, y, z] = rotXYZ(p, rx, ry)
            const isFront = z > 0
            if (frontPass !== isFront) { first = true; continue }
            const sx = cx + x * radius, sy = cy + y * radius
            if (first) { ctx.moveTo(sx, sy); first = false } else ctx.lineTo(sx, sy)
          }
          const a = frontPass ? 0.5 : 0.12
          ctx.strokeStyle = `rgba(${r},${g},${b},${a})`
          ctx.lineWidth   = frontPass ? 1.0 : 0.6
          ctx.stroke()
        }
      }
      drawMesh(false)

      // Particle points
      for (const p of points) {
        const [x, y, z] = rotXYZ(p, rx, ry)
        const sx = cx + x * radius, sy = cy + y * radius
        const depth = (z + 1) / 2
        const pop   = st === 'speaking'
          ? Math.max(0, Math.sin((y * 12) + t * 14) * amp) * 18
          : st === 'thinking' ? Math.sin((y * 5) + t * 6) * 4
          : amp * 3
        const sxd = sx + (x * pop), syd = sy + (y * pop)
        const a    = 0.18 + depth * 0.85
        const psize = 0.6 + depth * 1.6 + amp * 0.6
        ctx.fillStyle = `rgba(${r},${g},${b},${a})`
        ctx.beginPath(); ctx.arc(sxd, syd, psize, 0, Math.PI * 2); ctx.fill()
        if (depth > 0.85) {
          ctx.fillStyle = `rgba(${r},${g},${b},${0.05 + amp * 0.06})`
          ctx.beginPath(); ctx.arc(sxd, syd, psize * 4, 0, Math.PI * 2); ctx.fill()
        }
      }

      drawMesh(true)

      // Equator scan (speaking / thinking)
      if (st === 'speaking' || st === 'thinking') {
        const sweepY = Math.sin(t * (st === 'thinking' ? 3 : 5)) * radius * 0.7
        ctx.strokeStyle = `rgba(${r},${g},${b},.45)`
        ctx.lineWidth   = 1.25
        ctx.beginPath()
        ctx.ellipse(cx, cy + sweepY, radius * 0.95, radius * 0.18, 0, 0, Math.PI * 2)
        ctx.stroke()
      }

      // Working: double-helix outer ring
      if (st === 'working') {
        const outR = radius * 1.18
        for (let i = 0; i < 60; i++) {
          const a  = (i / 60) * Math.PI * 2 + t * 1.6
          const r2 = outR + Math.sin(a * 2) * 6
          ctx.fillStyle = `rgba(${r},${g},${b},${0.7 - (i / 60) * 0.4})`
          ctx.beginPath()
          ctx.arc(cx + Math.cos(a) * r2, cy + Math.sin(a) * r2 * 0.32, 1.4, 0, Math.PI * 2)
          ctx.fill()
        }
      }

      // Core flicker
      const coreR    = 6 + amp * 8
      const coreGrad = ctx.createRadialGradient(cx, cy, 0, cx, cy, coreR * 4)
      coreGrad.addColorStop(0,   `rgba(${r},${g},${b},.9)`)
      coreGrad.addColorStop(0.4, `rgba(${r},${g},${b},.25)`)
      coreGrad.addColorStop(1,   `rgba(${r},${g},${b},0)`)
      ctx.fillStyle = coreGrad
      ctx.beginPath(); ctx.arc(cx, cy, coreR * 4, 0, Math.PI * 2); ctx.fill()
      ctx.fillStyle = `rgba(255,255,255,${0.8 - (1 - amp) * 0.2})`
      ctx.beginPath(); ctx.arc(cx, cy, 1.6 + amp * 1.2, 0, Math.PI * 2); ctx.fill()

      raf = requestAnimationFrame(frame)
    }

    raf = requestAnimationFrame(frame)
    return () => cancelAnimationFrame(raf)
  }, [size, points, lines])

  return <canvas ref={canvasRef} style={{ display: 'block' }} />
}

// ── Orbital rings (SVG) ────────────────────────────────────────────────────────
export function OrbitalRings({ size = 540, accent = '#22d3ee' }) {
  const tref = useRef(null)
  useEffect(() => {
    let raf, t0 = performance.now()
    function loop(now) {
      const t = (now - t0) / 1000
      if (tref.current) {
        const r1 = tref.current.querySelector('.ring1')
        const r2 = tref.current.querySelector('.ring2')
        const r3 = tref.current.querySelector('.ring3')
        if (r1) r1.setAttribute('transform', `rotate(${t * 14} ${size / 2} ${size / 2})`)
        if (r2) r2.setAttribute('transform', `rotate(${-t * 22} ${size / 2} ${size / 2})`)
        if (r3) r3.setAttribute('transform', `rotate(${t * 6} ${size / 2} ${size / 2})`)
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [size])

  const c = size / 2, R1 = size * 0.46, R2 = size * 0.42, R3 = size * 0.5

  const ticks = []
  for (let i = 0; i < 60; i++) {
    const a = (i / 60) * Math.PI * 2
    const long = i % 5 === 0
    const r1 = R1, r2 = long ? R1 - 8 : R1 - 4
    ticks.push(
      <line key={i}
        x1={c + Math.cos(a) * r1} y1={c + Math.sin(a) * r1}
        x2={c + Math.cos(a) * r2} y2={c + Math.sin(a) * r2}
        stroke={accent} strokeOpacity={long ? .8 : .4} strokeWidth={long ? 1.2 : 0.8} />
    )
  }
  const dots = []
  for (let i = 0; i < 90; i++) {
    const a = (i / 90) * Math.PI * 2
    dots.push(
      <circle key={i} cx={c + Math.cos(a) * R3} cy={c + Math.sin(a) * R3}
        r={i % 6 === 0 ? 1.6 : 0.8}
        fill={accent} fillOpacity={i % 6 === 0 ? .7 : .25} />
    )
  }

  return (
    <svg ref={tref} className="wire" viewBox={`0 0 ${size} ${size}`} width={size} height={size}
      style={{ position: 'absolute', inset: 0, margin: 'auto', pointerEvents: 'none',
        filter: 'drop-shadow(0 0 6px rgba(34,211,238,.25))' }}>
      <g className="ring1">
        <circle cx={c} cy={c} r={R1} fill="none" stroke={accent} strokeOpacity=".25" />
        {ticks}
        <path
          d={`M ${c + Math.cos(-Math.PI / 3) * R1} ${c + Math.sin(-Math.PI / 3) * R1}
              A ${R1} ${R1} 0 0 1 ${c + Math.cos(Math.PI / 3) * R1} ${c + Math.sin(Math.PI / 3) * R1}`}
          fill="none" stroke={accent} strokeWidth="2" strokeDasharray="2 5" opacity=".9" />
      </g>
      <g className="ring2">
        <circle cx={c} cy={c} r={R2} fill="none" stroke={accent} strokeOpacity=".4"
          strokeDasharray="40 14 6 14 90 14 30 14" />
        {[0, 90, 180, 270].map(d => {
          const a = (d * Math.PI) / 180
          return <rect key={d} x={c + Math.cos(a) * R2 - 3} y={c + Math.sin(a) * R2 - 3}
            width="6" height="6" fill="none" stroke={accent} />
        })}
      </g>
      <g className="ring3">{dots}</g>
      {[['N', -Math.PI / 2], ['E', 0], ['S', Math.PI / 2], ['W', Math.PI]].map(([k, a]) => (
        <text key={k} x={c + Math.cos(a) * (R3 + 14)} y={c + Math.sin(a) * (R3 + 14)}
          textAnchor="middle" dominantBaseline="middle"
          fontSize="9" fill={accent} opacity=".6">{k}</text>
      ))}
    </svg>
  )
}

// ── Voice waveform bars ────────────────────────────────────────────────────────
export function VoiceBars({ state = 'idle', accent = '#22d3ee', count = 28 }) {
  const ref = useRef(null)
  useEffect(() => {
    let raf, t0 = performance.now()
    function loop(now) {
      const t = (now - t0) / 1000
      const el = ref.current; if (!el) return
      const bars = el.children
      for (let i = 0; i < bars.length; i++) {
        let h
        if (state === 'speaking')
          h = 6 + Math.abs(Math.sin(t * (4 + i * 0.4) + i)) * 22 + Math.abs(Math.sin(t * 11 + i * 2)) * 4
        else if (state === 'listening')
          h = 4 + Math.abs(Math.sin(t * 1.3 + i * 0.5)) * 10
        else if (state === 'thinking')
          h = 3 + ((i + Math.floor(t * 8)) % count) * 1.5
        else if (state === 'working')
          h = 4 + Math.abs(Math.sin(t * 3 + i)) * 14
        else
          h = 4 + Math.abs(Math.sin(t * 0.7 + i * 0.4)) * 4
        bars[i].style.height = h.toFixed(1) + 'px'
      }
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [state, count])
  return (
    <div ref={ref} className="orb-listen-bars">
      {Array.from({ length: count }).map((_, i) =>
        <span key={i} className="bar-" style={{ background: accent, boxShadow: `0 0 6px ${accent}` }} />
      )}
    </div>
  )
}
