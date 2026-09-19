// FISHRAND dashboard API client.
// POST -> SSE: fetch returns a ReadableStream we parse line-by-line
// for `event:` / `data:` frames.

const API_BASE = import.meta.env.VITE_API_BASE || ''

export async function currentObservations() {
  const res = await fetch(`${API_BASE}/api/observations/current`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`current: HTTP ${res.status}`)
  return res.json()
}

// Validate a fish payload against the backend contract. Posts it as the new
// observed window (so what you're looking at is what gets bound), and returns
// the resulting metadata.
export async function validateObservations(fishJson) {
  const res = await fetch(`${API_BASE}/api/observations`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ observations: fishJson }),
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const err = await res.json()
      detail = err.detail || detail
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  const current = await currentObservations()
  return current?.metadata || {}
}

// Persistent SSE feed: pushes `fish_update` whenever the broker publishes a
// new observation window (teammate's vision engine, manual push, ...).
// Returns an EventSource; events carry {source, received_at, metadata}.
export function subscribeFish(onUpdate) {
  const es = new EventSource(`${API_BASE}/api/events`)
  es.addEventListener('fish_update', (msg) => {
    try {
      onUpdate(JSON.parse(msg.data))
    } catch { /* ignore malformed frame */ }
  })
  return es
}

// Latest ESP32 mic window the server has (cached from the background audio
// poller, or a prior manual capture). null if none yet.
export async function currentAudio() {
  const res = await fetch(`${API_BASE}/api/audio/current`)
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`current: HTTP ${res.status}`)
  return res.json()
}

// Persistent SSE feed: pushes `audio_update` whenever the server's
// background audio poller captures a new ESP32 Sound_Level window.
// Returns an EventSource; events carry {source, received_at, metadata}.
export function subscribeAudio(onUpdate) {
  const es = new EventSource(`${API_BASE}/api/audio/events`)
  es.addEventListener('audio_update', (msg) => {
    try {
      onUpdate(JSON.parse(msg.data))
    } catch { /* ignore malformed frame */ }
  })
  return es
}

// Telemetry snapshot (fish activity history, quality-tier counts,
// encrypt/decrypt rate, HKDF + AES/RSA-wrap latency percentiles).
export async function fetchMetrics() {
  const res = await fetch(`${API_BASE}/api/metrics`)
  if (!res.ok) throw new Error(`metrics: HTTP ${res.status}`)
  return res.json()
}

// Persistent SSE feed: pushes `metrics_update` on the server's own
// broadcast cadence (server/config.py's FISHRAND_METRICS_INTERVAL_S).
export function subscribeMetrics(onUpdate) {
  const es = new EventSource(`${API_BASE}/api/metrics/events`)
  es.addEventListener('metrics_update', (msg) => {
    try {
      onUpdate(JSON.parse(msg.data))
    } catch { /* ignore malformed frame */ }
  })
  return es
}

// Cosmetic LOCKED/UNLOCKED relay the diary app pushes on lock()/unlock() -
// never the key or any content, see server/main.py's module docstring.
export async function fetchVaultStatus() {
  const res = await fetch(`${API_BASE}/api/vault/status`)
  if (!res.ok) throw new Error(`vault status: HTTP ${res.status}`)
  return res.json()
}

export function subscribeVaultStatus(onUpdate) {
  const es = new EventSource(`${API_BASE}/api/vault/events`)
  es.addEventListener('vault_update', (msg) => {
    try {
      onUpdate(JSON.parse(msg.data))
    } catch { /* ignore malformed frame */ }
  })
  return es
}

// vision.py's live camera preview, proxied through this server (it never
// touches a camera itself - see server/main.py's /api/vision/* routes).
export const VISION_STREAM_URL = `${API_BASE}/api/vision/stream`
export const VISION_SNAPSHOT_URL = `${API_BASE}/api/vision/snapshot`

// Runs a request and streams `pipe` events to onPipe; resolves with the
// `done` frame payload.
export async function streamPipeline(path, body, onPipe) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const err = await res.json()
      detail = err.detail || detail
    } catch { /* ignore */ }
    throw new Error(detail)
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let eventType = ''
  let donePayload = null
  let rejected = null

  function handleLine(line) {
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      const data = line.slice(5).trim()
      if (eventType === 'pipe') {
        onPipe(JSON.parse(data))
      } else if (eventType === 'done') {
        const parsed = JSON.parse(data)
        if (parsed.status === 'rejected' && !rejected) rejected = parsed
        donePayload = parsed
      }
    } else if (line === '') {
      eventType = ''
    }
  }

  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, idx)
      buffer = buffer.slice(idx + 1)
      handleLine(line.replace(/\r$/, ''))
    }
  }

  if (rejected) {
    throw new Error(rejected.reason || 'AUTHENTICATION FAILED')
  }
  return donePayload
}