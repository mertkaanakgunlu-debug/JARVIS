import { useState, useEffect } from 'react'

export default function useClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  const pad = n => n.toString().padStart(2, '0')
  return {
    time: `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`,
    date: now.toLocaleDateString('en-US', { weekday: 'short', year: 'numeric', month: 'short', day: '2-digit' }).toUpperCase(),
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    session: Math.floor(Date.now() / 1000).toString(16).slice(-5).toUpperCase(),
  }
}
