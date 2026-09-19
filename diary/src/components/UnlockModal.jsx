import { useRef, useState } from 'react'
import { readTextFile } from '../api'
import { useDiary } from '../vault/DiaryContext'
import Icon from './Icon'
import KeepUnlockedToggle from './KeepUnlockedToggle'

export default function UnlockModal({ onClose }) {
  const { unlock, unlockWithKeysFolder, keysFolderSupported, entries, unlockProgress } = useDiary()
  const [mode, setMode] = useState(keysFolderSupported ? 'folder' : 'paste')
  const [pem, setPem] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const fileRef = useRef(null)

  async function onFile(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (file) setPem((await readTextFile(file)).trim())
  }

  async function submit(e) {
    e.preventDefault()
    setBusy(true)
    setError('')
    setResult(null)
    try {
      if (mode === 'folder') {
        const res = await unlockWithKeysFolder()
        setResult(res)
      } else {
        await unlock(pem)
        setPem('')
        onClose()
      }
    } catch (err) {
      if (err?.name !== 'AbortError') setError(String(err.message || err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <form className="modal" onSubmit={submit}>
        <div className="modal-head">
          <h2><Icon name="lockClosed" size={16} /> Unlock the vault</h2>
          <button type="button" className="icon-btn" onClick={onClose} disabled={busy} aria-label="Close"><Icon name="close" size={13} /></button>
        </div>

        {keysFolderSupported && (
          <div className="tabs" role="tablist">
            <button type="button" className={mode === 'folder' ? 'active' : ''} onClick={() => setMode('folder')}><Icon name="folder" size={13} /> Keys folder</button>
            <button type="button" className={mode === 'paste' ? 'active' : ''} onClick={() => setMode('paste')}><Icon name="clipboard" size={13} /> Paste key</button>
          </div>
        )}

        {mode === 'folder' ? (
          <>
            <p className="muted">
              Every entry has its own key file, saved to your USB drive when you wrote it. Point at that folder and
              every key in it will be tried against every sealed entry.
            </p>
            {result && (
              <p className="muted">Opened {result.opened} of {result.total} sealed entries using {result.keysTried} key file(s).</p>
            )}
          </>
        ) : (
          <>
            <p className="muted">
              Paste an entry's <code>.pem</code> key or pick the file. It’s sent only with each decrypt
              request and kept in this tab’s memory, never in browser storage.
            </p>
            <textarea
              className="pem-input mono"
              rows={6}
              value={pem}
              onChange={(e) => setPem(e.target.value)}
              placeholder="-----BEGIN PRIVATE KEY-----"
              spellCheck={false}
              autoComplete="off"
            />
            <div className="row">
              <button type="button" className="btn ghost" onClick={() => fileRef.current?.click()}>Choose .pem file…</button>
              <input ref={fileRef} type="file" accept=".pem,.txt" hidden onChange={onFile} />
              <span className="muted small">{entries.length} sealed {entries.length === 1 ? 'entry' : 'entries'}</span>
            </div>
          </>
        )}

        <KeepUnlockedToggle />
        {busy && unlockProgress && (
          <div className="progress" aria-label="decrypting">
            <div style={{ width: `${unlockProgress.total ? (unlockProgress.done / unlockProgress.total) * 100 : 100}%` }} />
          </div>
        )}
        {error && <div className="alert bad">{error.slice(0, 200)}</div>}
        <div className="modal-actions">
          <button type="button" className="btn ghost" onClick={onClose} disabled={busy}>{result ? 'Done' : 'Cancel'}</button>
          {mode === 'paste' && (
            <button type="submit" className="btn primary" disabled={busy || !pem.trim()}>
              {busy ? 'Checking key…' : 'Unlock'}
            </button>
          )}
          {mode === 'folder' && (
            <button type="submit" className="btn primary" disabled={busy}>
              {busy ? 'Checking keys…' : result ? 'Try another folder' : 'Choose keys folder…'}
            </button>
          )}
        </div>
      </form>
    </div>
  )
}
