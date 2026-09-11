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