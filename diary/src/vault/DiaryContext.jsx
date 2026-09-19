import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { createEntry, decryptPackage, listEntries, postVaultStatus, purgeEntry, setEntryFlags } from '../api'
import {
  getStoredHandle, permissionState, pickFolder, readPemFiles, requestPermission,
  SUPPORTED as KEYS_FOLDER_SUPPORTED,
} from '../lib/keysFolder'
import { decodeEntry, encodeEntry } from '../lib/payload'

const DiaryContext = createContext(null)

const IDLE_LOCK_MS = 10 * 60 * 1000
const HIDDEN_LOCK_MS = 2 * 60 * 1000
const DECRYPT_CONCURRENCY = 3

// Run `worker` over `items` with at most `limit` in flight.
async function pool(items, limit, worker) {
  let next = 0
  async function lane() {
    while (next < items.length) await worker(items[next++])
  }
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, lane))
}

export function DiaryProvider({ children }) {
  // --- ciphertext side: safe to hold whether locked or not -----------------
  const [entries, setEntries] = useState([])
  const [keyExists, setKeyExists] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')

  // --- vault: the private key and plaintext, in memory ONLY ----------------
  // The PEM sits in a ref (not state, so it never lands in React devtools'
  // state snapshots) and is never written to localStorage, sessionStorage,
  // IndexedDB or anywhere else. Reloading or closing the tab drops it.
  const privateKeyRef = useRef(null)
  const attemptedRef = useRef(new Set()) // ids already tried with the current key
  const [unlocked, setUnlocked] = useState(false)
  const [decrypted, setDecrypted] = useState(() => new Map())
  const [keepUnlocked, setKeepUnlocked] = useState(false)
  const [unlockProgress, setUnlockProgress] = useState(null) // { done, total } while unlocking
  // Why a still-sealed entry won't open - e.g. { code: 'drive_missing', message }
  // from a correct key whose required physical drive isn't plugged in. A
  // 'drive_missing' reason is never overwritten by a later plain wrong-key
  // failure from trying a different key, so the most useful reason wins.
  const [sealReasons, setSealReasons] = useState(() => new Map())

  const noteSealReason = useCallback((id, err) => {
    if (!err?.code) return
    setSealReasons((prev) => {
      if (prev.get(id)?.code === 'drive_missing') return prev
      return new Map(prev).set(id, { code: err.code, message: String(err.message || err) })
    })
  }, [])

  const refresh = useCallback(async () => {
    try {
      const state = await listEntries()
      setEntries(state.entries)
      setKeyExists(!!state.encryption_key_exists)
      setLoadError('')
    } catch (err) {
      setLoadError(String(err.message || err))
    } finally {
      setLoading(false)
    }
  }, [])

  // Initial load from the server (an external system, so an effect is right).
  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { refresh() }, [refresh])

  const lock = useCallback(() => {
    privateKeyRef.current = null
    attemptedRef.current = new Set()
    setDecrypted(new Map())
    setSealReasons(new Map())
    setUnlocked(false)
    setUnlockProgress(null)
    postVaultStatus(false).catch(() => {}) // best-effort - see api.js
  }, [])

  const decryptInto = useCallback(async (targets, pem) => {
    targets.forEach((entry) => attemptedRef.current.add(entry.id))
    await pool(targets, DECRYPT_CONCURRENCY, async (entry) => {
      try {
        const content = decodeEntry(await decryptPackage(entry.package, pem))
        if (privateKeyRef.current !== pem) return // locked meanwhile
        setDecrypted((prev) => new Map(prev).set(entry.id, content))
      } catch (err) {
        // One corrupt/foreign package shouldn't fail the whole vault; its
        // card just stays sealed, optionally with a reason (e.g. its
        // required drive isn't plugged in).
        noteSealReason(entry.id, err)
      }
    })
  }, [noteSealReason])

  // Throws if the key opens none of the entries. It is accepted if it opens
  // at least one: entries sealed for an older keypair just stay sealed.
  const unlock = useCallback(async (pem) => {
    const key = pem.trim()
    if (!key) throw new Error('Paste or load your RSA private key first.')
    const opened = new Map()
    let firstError = null
    setUnlockProgress({ done: 0, total: entries.length })
    try {
      await pool(entries, DECRYPT_CONCURRENCY, async (entry) => {
        try {
          opened.set(entry.id, decodeEntry(await decryptPackage(entry.package, key)))
        } catch (err) {
          firstError ??= err
          noteSealReason(entry.id, err)
        }
        setUnlockProgress((p) => (p ? { ...p, done: p.done + 1 } : p))
      })
    } finally {
      setUnlockProgress(null)
    }
    if (entries.length && !opened.size) throw firstError
    privateKeyRef.current = key
    attemptedRef.current = new Set(entries.map((e) => e.id))
    setDecrypted(opened)
    setUnlocked(true)
    postVaultStatus(true).catch(() => {}) // best-effort - see api.js
    return { opened: opened.size, total: entries.length }
  }, [entries, noteSealReason])

  // New model: every entry has its OWN keypair (server/main.py's
  // /api/diary/entries), so opening old entries means trying every .pem in
  // a USB folder against every still-sealed entry, rather than one shared
  // key against all of them. Throws if the folder had no keys, or none of
  // its keys opened anything; partial success (some entries stay sealed)
  // is fine, same tolerance as the single-key unlock() above.
  const unlockWithKeysFolder = useCallback(async () => {
    let handle = await getStoredHandle()
    if (handle) {
      const state = await permissionState(handle)
      if (state !== 'granted') {
        const granted = await requestPermission(handle).catch(() => 'denied')
        if (granted !== 'granted') handle = null
      }
    }
    if (!handle) handle = await pickFolder()

    const files = await readPemFiles(handle)
    if (!files.length) throw new Error('No .pem key files found in that folder.')

    const sealed = entries.filter((e) => !decrypted.has(e.id))
    const opened = new Map()
    let firstError = null
    setUnlockProgress({ done: 0, total: sealed.length })
    try {
      await pool(sealed, DECRYPT_CONCURRENCY, async (entry) => {
        let bestErr = null // prefer a 'drive_missing' reason over a plain wrong-key one
        for (const { pem } of files) {
          try {
            opened.set(entry.id, decodeEntry(await decryptPackage(entry.package, pem)))
            bestErr = null
            break
          } catch (err) {
            firstError ??= err
            if (!bestErr || err.code === 'drive_missing') bestErr = err
          }
        }
        if (bestErr) noteSealReason(entry.id, bestErr)
        setUnlockProgress((p) => (p ? { ...p, done: p.done + 1 } : p))
      })
    } finally {
      setUnlockProgress(null)
    }
    if (sealed.length && !opened.size) throw firstError ?? new Error('None of the keys in that folder opened any entry.')

    setDecrypted((prev) => new Map([...prev, ...opened]))
    sealed.forEach((e) => attemptedRef.current.add(e.id))
    setUnlocked(true)
    postVaultStatus(true).catch(() => {}) // best-effort - see api.js
    return { opened: opened.size, total: sealed.length, keysTried: files.length }
  }, [entries, decrypted, noteSealReason])

  // Entries that appear while unlocked (another tab, an import) get
  // decrypted in the background.
  useEffect(() => {
    const pem = privateKeyRef.current
    if (!unlocked || !pem || unlockProgress) return
    const missing = entries.filter((e) => !attemptedRef.current.has(e.id))
    if (missing.length) decryptInto(missing, pem)
  }, [entries, unlocked, unlockProgress, decryptInto])

  // Auto-lock unless "Keep unlocked for this session" is on.
  useEffect(() => {
    if (!unlocked || keepUnlocked) return
    let idleTimer = setTimeout(lock, IDLE_LOCK_MS)
    let hiddenTimer = null
    const bump = () => {
      clearTimeout(idleTimer)
      idleTimer = setTimeout(lock, IDLE_LOCK_MS)
    }
    const onVisibility = () => {
      clearTimeout(hiddenTimer)
      if (document.hidden) hiddenTimer = setTimeout(lock, HIDDEN_LOCK_MS)
    }
    const activity = ['pointerdown', 'keydown', 'scroll']
    activity.forEach((evt) => window.addEventListener(evt, bump, { passive: true }))
    document.addEventListener('visibilitychange', onVisibility)
    return () => {
      clearTimeout(idleTimer)
      clearTimeout(hiddenTimer)
      activity.forEach((evt) => window.removeEventListener(evt, bump))
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [unlocked, keepUnlocked, lock])

  const saveEntry = useCallback(async ({ body, mood }, driveSerial, onPipe) => {
    const done = await createEntry(encodeEntry({ body, mood }), driveSerial, onPipe)
    const entry = { id: done.id, package: done.package, flags: done.flags }
    setEntries((prev) => [entry, ...prev])
    // We already have this plaintext in hand - show it immediately
    // regardless of vault-lock state, which only governs OLDER entries.
    attemptedRef.current.add(entry.id)
    setDecrypted((prev) => new Map(prev).set(entry.id, { body, mood }))
    // done.private_key_pem is this ONE entry's key - the server never kept
    // a copy. Hand it back so the composer can get it saved before it's lost.
    return { ...entry, privateKeyPem: done.private_key_pem }
  }, [])

  const updateFlags = useCallback(async (id, flags) => {
    const res = await setEntryFlags(id, flags)
    setEntries((prev) => prev.map((e) => (e.id === id ? { ...e, flags: res.flags } : e)))
  }, [])

  const purge = useCallback(async (id) => {
    await purgeEntry(id)
    setEntries((prev) => prev.filter((e) => e.id !== id))
    setDecrypted((prev) => {
      const next = new Map(prev)
      next.delete(id)
      return next
    })
  }, [])

  // For the Vault page's .pkg import: needs the in-memory key.
  const decryptWithVaultKey = useCallback(async (pkg) => {
    if (!privateKeyRef.current) throw new Error('Unlock the vault first.')
    return decodeEntry(await decryptPackage(pkg, privateKeyRef.current))
  }, [])

  const value = useMemo(() => ({
    entries, keyExists, loading, loadError, refresh,
    unlocked, decrypted, sealReasons, unlock, unlockWithKeysFolder, keysFolderSupported: KEYS_FOLDER_SUPPORTED,
    lock, unlockProgress,
    keepUnlocked, setKeepUnlocked,
    saveEntry, updateFlags, purge, decryptWithVaultKey,
  }), [entries, keyExists, loading, loadError, refresh, unlocked, decrypted, sealReasons, unlock,
    unlockWithKeysFolder, lock, unlockProgress, keepUnlocked, saveEntry, updateFlags, purge, decryptWithVaultKey])

  return <DiaryContext.Provider value={value}>{children}</DiaryContext.Provider>
}

// oxlint-disable-next-line react/only-export-components
export function useDiary() {
  const ctx = useContext(DiaryContext)
  if (!ctx) throw new Error('useDiary must be used inside <DiaryProvider>')
  return ctx
}
