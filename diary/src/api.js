// FISHRAND diary API client. Same SSE-pipeline protocol as the dashboard.

const API_BASE = import.meta.env.VITE_API_BASE || ''

export function readTextFile(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error)
    reader.readAsText(file)
  })
}

export async function parsePackageFile(file) {
  const text = await readTextFile(file)
  return JSON.parse(text)
}

// GET the state of the one persistent encrypted diary stored on this PC's
// server. Never returns plaintext - ciphertext + public metadata only.
export async function fetchDiaryState() {
  const res = await fetch(`${API_BASE}/api/diary`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export function downloadPackage(pkg, name) {
  const blob = new Blob([JSON.stringify(pkg, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = name
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

// Post to /api/encrypt or /api/decrypt, streaming `pipe` events to onPipe;
// resolves with the `done` frame payload, throwing on `rejected`.
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