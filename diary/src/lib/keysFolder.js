// Remembers the USB folder of per-entry private_key .pem files, so the
// person doesn't have to browse to it every time they reopen the diary.
// Only a FileSystemDirectoryHandle is stored (a browser-native handle
// object, not the key files themselves) - IndexedDB persists it across
// reloads, but the browser still requires one click to re-grant read
// permission each time the page loads; that is a browser security rule,
// not something this code can skip.

const DB_NAME = 'meencrypt-keys-folder'
const STORE = 'handle'
const KEY = 'folder'

export const SUPPORTED = typeof window !== 'undefined' && !!window.showDirectoryPicker

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

export async function getStoredHandle() {
  try {
    return (await run('readonly', (store) => store.get(KEY))) || null
  } catch {
    return null
  }
}

export function setStoredHandle(handle) {
  return run('readwrite', (store) => store.put(handle, KEY))
}

// 'granted' | 'prompt' | 'denied' | null (no stored handle).
export async function permissionState(handle) {
  if (!handle) return null
  try {
    return await handle.queryPermission({ mode: 'read' })
  } catch {
    return null
  }
}

export function requestPermission(handle) {
  return handle.requestPermission({ mode: 'read' })
}

export async function pickFolder() {
  const handle = await window.showDirectoryPicker({ mode: 'read' })
  await setStoredHandle(handle)
  return handle
}

// Every .pem file directly inside the folder, as { name, pem } pairs.
export async function readPemFiles(dirHandle) {
  const files = []
  for await (const [name, entry] of dirHandle.entries()) {
    if (entry.kind !== 'file' || !name.toLowerCase().endsWith('.pem')) continue
    const file = await entry.getFile()
    files.push({ name, pem: (await file.text()).trim() })
  }
  return files
}
