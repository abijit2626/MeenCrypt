import { useEffect, useState } from 'react'
import { fetchMetrics, subscribeMetrics } from '../api'

const EMPTY = {
  fish_activity: [],
  quality_tiers: { GOOD: 0, MEDIUM: 0, BAD: 0 },
  ops: {
    encrypt: { rate_per_min: 0, history: [] },
    decrypt: { rate_per_min: 0, history: [] },
  },
  latency_ms: {
    kdf: { p50: null, p95: null, n: 0 },
    wrap: { p50: null, p95: null, n: 0 },
  },
}

// One SSE connection to /api/metrics/events, shared by every telemetry
// panel - instantiate this ONCE (in DashboardView) and pass the result
// down, same as useFishCrypto() is instantiated once in App.jsx.
export function useMetrics() {
  const [metrics, setMetrics] = useState(EMPTY)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    let cancelled = false
    fetchMetrics().then((m) => { if (!cancelled) setMetrics(m) }).catch(() => {})

    const es = subscribeMetrics((m) => setMetrics(m))
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    return () => {
      cancelled = true
      es.close()
    }
  }, [])

  return { ...metrics, connected }
}
