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

// Every entry now gets its OWN keypair (see /api/diary/entries), so its
// private key needs to be saved somewhere real (the USB) right away - it
// is never kept server-side. Prefers a native "Save As" dialog so the
// person picks the drive themselves; falls back to a normal download on
// browsers without the File System Access API (Firefox/Safari).
// Returns true if a real Save As dialog was used, false for the fallback
// download, or throws AbortError-named if the person cancelled the dialog.
export async function savePrivateKeyPem(pem, suggestedName) {
  if (window.showSaveFilePicker) {
    const handle = await window.showSaveFilePicker({
      suggestedName,
      types: [{ description: 'PEM private key', accept: { 'application/x-pem-file': ['.pem'] } }],
    })
    const writable = await handle.createWritable()
    await writable.write(pem)
    await writable.close()
    return true
  }
  const blob = new Blob([pem], { type: 'application/x-pem-file' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = suggestedName
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
  return false
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

// POST /api/keys/generate. The server makes a fresh RSA-3072 keypair,
// keeps ONLY the public key, and hands back the private key PEM directly
// in the response body — never saved server-side. We trigger an ordinary
// browser download of it as private_key.pem, mirroring downloadPackage().
// `force: true` is required if a public key already exists (regenerating
// orphans any diary already encrypted with the old key).
export async function generateKeypair(force = false) {
  const res = await fetch(`${API_BASE}/api/keys/generate`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ force }),
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const err = await res.json()
      detail = err.detail || detail
    } catch { /* ignore */ }
    throw new Error(detail)
  }
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = 'private_key.pem'
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

async function jsonRequest(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { 'content-type': 'application/json', ...(options.headers || {}) },
  })
  if (!res.ok) {
    let detail = `HTTP ${res.status}`
    try {
      const err = await res.json()
      detail = err.detail || detail
    } catch { /* ignore */ }
    const error = new Error(detail)
    error.status = res.status
    throw error
  }
  return res.json()
}

// --- multi-entry diary: ciphertext packages + plaintext flags only -------
export function listEntries() {
  return jsonRequest('/api/diary/entries')
}

// Currently mounted drives with a readable hardware serial - candidates
// for binding a new entry's key to. Checked fresh every call.
export async function fetchDrives() {
  const res = await jsonRequest('/api/vault/drives')
  return res.drives
}

// `plaintext` is already the encoded entry payload (body + mood), so the
// mood is encrypted along with the text - see lib/payload.js. `driveSerial`
// is baked into the entry so decrypting it later also requires that exact
// physical drive present (see fetchDrives / server/main.py create_entry).
export function createEntry(plaintext, driveSerial, onPipe = () => {}) {
  return streamPipeline('/api/diary/entries', { plaintext, drive_serial: driveSerial }, onPipe)
}

export function setEntryFlags(id, flags) {
  return jsonRequest(`/api/diary/entries/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(flags),
  })
}

export function purgeEntry(id) {
  return jsonRequest(`/api/diary/entries/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

// The private key travels in this one request body and nowhere else.
export async function decryptPackage(pkg, privateKeyPem, onPipe = () => {}) {
  const payload = await streamPipeline('/api/decrypt', { package: pkg, private_key_pem: privateKeyPem }, onPipe)
  return payload.plaintext
}

// Cosmetic LOCKED/UNLOCKED relay so OTHER apps (the dashboard) can show
// this diary's vault state too - a plain boolean, never the private key
// or any decrypted content. Fire-and-forget: DiaryContext calls this from
// lock()/unlock(), and a network hiccup here must never affect the actual
// lock/unlock flow, so callers should swallow rejections.
export function postVaultStatus(unlocked) {
  return jsonRequest('/api/vault/status', { method: 'POST', body: JSON.stringify({ unlocked }) })
}

// Live tank/mic windows, used only for the composer's status line.
// Resolve to null when the server has nothing yet (404).
export async function fetchObservation() {
  try {
    return await jsonRequest('/api/observations/current')
  } catch (err) {
    if (err.status === 404) return null
    throw err
  }
}

export async function fetchAudio() {
  try {
    return await jsonRequest('/api/audio/current')
  } catch (err) {
    if (err.status === 404) return null
    throw err
  }
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
    const error = new Error(rejected.reason || 'AUTHENTICATION FAILED')
    error.code = rejected.code // e.g. 'drive_missing' vs 'auth_failed' - see server/main.py _run_decrypt
    throw error
  }
  return donePayload
}