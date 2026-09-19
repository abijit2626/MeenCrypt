// Optional autosave of the in-progress composer draft to IndexedDB.
// The draft is PLAINTEXT until it's encrypted and saved - the composer
// says so whenever this is on. Only { body, mood, updatedAt } is ever
// written here; never a key.

const DB_NAME = 'meencrypt-drafts'
const STORE = 'draft'
const KEY = 'current'

function openDb() {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => req.result.createObjectStore(STORE)
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error)
  })
}

async function run(mode, fn) {
  const db = await openDb()
  try {
    return await new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode)
      const req = fn(tx.objectStore(STORE))
      tx.oncomplete = () => resolve(req.result)
      tx.onerror = () => reject(tx.error)
    })
  } finally {
    db.close()
  }
}

export async function loadDraft() {
  try {
    // Don't create the database just by looking - only autosave does that.
    const existing = await indexedDB.databases?.()
    if (existing && !existing.some((db) => db.name === DB_NAME)) return null
    return (await run('readonly', (store) => store.get(KEY))) || null
  } catch {
    return null
  }
}

export function saveDraft({ body, mood }) {
  return run('readwrite', (store) => store.put({ body, mood, updatedAt: Date.now() }, KEY))
}

export async function clearDraft() {
  try {
    await run('readwrite', (store) => store.delete(KEY))
  } catch { /* nothing stored */ }
}
