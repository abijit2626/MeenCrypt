import { useEffect, useRef, useState } from 'react'
import { downloadPackage, fetchDiaryState, generateKeypair, parsePackageFile, readTextFile, streamPipeline } from './api'
import './App.css'

const FRESH_PLACEHOLDER = `Dear future me,

I hid the spare fish flakes behind the power strip.
Do not tell Fibonacci. He has a fragile ego.

- me, trusting a fish (and my RSA private key) with my secrets`

// Compact live log of the crypto pipeline events coming over SSE.
function PipelineLog({ events }) {
  if (!events.length) return <p className="hint">pipeline events will stream here…</p>
  return (
    <div className="pipeline-scroll" style={{ maxHeight: 260 }}>
      {events.map((evt, i) => (
        <div key={i} className={`step ${evt.status === 'error' ? 'bad' : 'ok'}`}>
          <div className="step-line">
            <span className="step-dot" />
            <span className="step-label">{String(evt.step).toUpperCase()}</span>
            <span className={`step-status ${evt.status === 'error' ? 'bad' : 'ok'}`}>
              {evt.status === 'error' ? 'FAILED' : String(evt.status).toUpperCase()}
            </span>
            {evt.duration_ms != null && <span className="step-dur mono">{evt.duration_ms} ms</span>}
          </div>
          {evt.detail && Object.entries(evt.detail).map(([k, v]) => (
            <div className="step-detail mono" key={k}>
              <span>{k}:</span>
              <span className={k === 'key_material' || k === 'secret' ? 'hidden' : ''}>{String(v)}</span>
            </div>
          ))}
        </div>
      ))}
    </div>
  )
}

export default function App() {
  // --- the one persistent diary document on this PC (server-side) -------
  const [loadingState, setLoadingState] = useState(true)
  const [hasStored, setHasStored] = useState(false)
  const [savedAt, setSavedAt] = useState(null)
  const [unlocked, setUnlocked] = useState(false)
  const [keyExists, setKeyExists] = useState(false)

  const [diary, setDiary] = useState('')
  const [privateKeyPem, setPrivateKeyPem] = useState('')

  const [saveLog, setSaveLog] = useState([])
  const [unlockLog, setUnlockLog] = useState([])
  const [saveStatus, setSaveStatus] = useState('idle')     // idle|running|done|error
  const [unlockStatus, setUnlockStatus] = useState('idle')
  const [saveErr, setSaveErr] = useState('')
  const [unlockErr, setUnlockErr] = useState('')

  const [genBusy, setGenBusy] = useState(false)
  const [genErr, setGenErr] = useState('')

  const [lastPkg, setLastPkg] = useState(null)              // for the backup-export button
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [importErr, setImportErr] = useState('')

  const privateKeyRef = useRef(null)
  const importRef = useRef(null)

  async function refreshDiaryState() {
    setLoadingState(true)
    try {
      const state = await fetchDiaryState()
      setHasStored(!!state.exists)
      setSavedAt(state.saved_at || null)
      setLastPkg(state.exists ? state.package : null)
      setKeyExists(!!state.encryption_key_exists)
    } catch {
      // server not reachable yet / transient — leave state as-is, user can retry
    } finally {
      setLoadingState(false)
    }
  }

  useEffect(() => {
    refreshDiaryState()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function onLoadPrivateKey(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const text = (await readTextFile(file)).trim()
    setPrivateKeyPem(text)
    e.target.value = ''
  }

  async function onGenerateKeypair() {
    if (keyExists) {
      const ok = window.confirm(
        'A key already exists. Generating a new one makes any diary already ' +
        'saved on this PC permanently unreadable (it was encrypted for the old key). Continue?'
      )
      if (!ok) return
    }
    setGenErr('')
    setGenBusy(true)
    try {
      await generateKeypair(keyExists)
      await refreshDiaryState()
    } catch (err) {
      setGenErr(String(err.message || err))
    } finally {
      setGenBusy(false)
    }
  }

  async function save() {
    if (!diary.trim()) { setSaveErr('Write something first.'); return }
    setSaveStatus('running')
    setSaveErr('')
    setSaveLog([])
    try {
      const payload = await streamPipeline(
        '/api/diary/save',
        { plaintext: diary },
        (evt) => setSaveLog((prev) => [...prev, evt]),
      )
      setSaveStatus('done')
      setHasStored(true)
      setUnlocked(true)   // the textarea now IS the true saved content
      setSavedAt(payload.saved_at || null)
      setLastPkg(payload.package)
    } catch (err) {
      setSaveErr(String(err.message || err))
      setSaveStatus('error')
    }
  }

  async function unlock() {
    if (!hasStored) { setUnlockErr('Nothing saved on this PC yet.'); return }
    if (!privateKeyPem.trim()) { setUnlockErr('Load your RSA private key (private_key.pem) first.'); return }
    setUnlockStatus('running')
    setUnlockErr('')
    setUnlockLog([])
    try {
      const payload = await streamPipeline(
        '/api/diary/unlock',
        { private_key_pem: privateKeyPem.trim() },
        (evt) => setUnlockLog((prev) => [...prev, evt]),
      )
      setDiary(payload.plaintext)
      setUnlocked(true)
      setUnlockStatus('done')
    } catch (err) {
      setUnlockErr(String(err.message || err))
      setUnlockStatus('error')
    }
  }

  // --- advanced: restore from a previously exported .pkg backup ---------
  async function onImportPackage(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (!privateKeyPem.trim()) { setImportErr('Load your RSA private key (private_key.pem) first.'); return }
    setImportErr('')
    setUnlockStatus('running')
    setUnlockLog([])
    try {
      const imported = await parsePackageFile(file)
      const payload = await streamPipeline(
        '/api/decrypt',
        { package: imported, private_key_pem: privateKeyPem.trim() },
        (evt) => setUnlockLog((prev) => [...prev, evt]),
      )
      setDiary(payload.plaintext)
      setUnlocked(true)
      setUnlockStatus('done')
      setImportErr(`loaded ${file.name} — click "Save" below to make this the diary stored on this PC`)
    } catch (err) {
      setUnlockStatus('error')
      setImportErr(String(err.message || err))
    }
  }

  const showFreshPlaceholder = !loadingState && !hasStored
  const showLockedPlaceholder = !loadingState && hasStored && !unlocked

  return (
    <div className="layout">
      <header className="topbar">
        <div>
          <h1>MeenCrypt · Diary</h1>
          <p className="sub">a diary kept encrypted on this PC — only your RSA private key opens it</p>
        </div>
        <nav className="nav">
          <span className="tag">save → stored encrypted, on this PC</span>
          <span className="tag">unlock → your private key required</span>
        </nav>
      </header>

      <div className="diary-layout">
        <section className="panel diary-writer">
          <div className="panel-head">
            <h2>📔 The secret diary</h2>
            {loadingState && <span className="tag">checking for a saved diary…</span>}
            {showFreshPlaceholder && <span className="tag">nothing saved here yet</span>}
            {showLockedPlaceholder && <span className="tag bad">🔒 locked — encrypted on disk</span>}
            {unlocked && <span className="tag ok">🔓 unlocked</span>}
          </div>
          <textarea
            className="diary-big"
            rows={20}
            spellCheck={false}
            placeholder={
              showLockedPlaceholder
                ? '🔒 A diary is saved here, encrypted. Load your private key and click Unlock to read it — or just start typing to overwrite it with a fresh entry.'
                : 'Write your deepest secrets here…'
            }
            value={showFreshPlaceholder && diary === '' ? '' : diary}
            onChange={(e) => { setDiary(e.target.value); if (showLockedPlaceholder) setUnlocked(false) }}
            onFocus={() => { if (showFreshPlaceholder && diary === '') setDiary(FRESH_PLACEHOLDER) }}
          />
          {savedAt && <p className="hint">last saved on this PC: {new Date(savedAt).toLocaleString()}</p>}
        </section>

        <div className="diary-ctrl">
          <section className="panel">
            <div className="panel-head">
              <h2>🔑 Your RSA key</h2>
              <div className="panel-actions">
                <button className="btn ghost" onClick={onGenerateKeypair} disabled={genBusy}>
                  {genBusy ? 'Generating…' : keyExists ? '🔁 Generate new keypair' : '✨ Generate keypair'}
                </button>
                <button className="btn ghost" onClick={() => privateKeyRef.current?.click()}>load private_key.pem</button>
                <input ref={privateKeyRef} type="file" accept=".pem,.txt" hidden onChange={onLoadPrivateKey} />
              </div>
            </div>
            <div className="row-between">
              <span className={`tag ${keyExists ? 'ok' : 'bad'}`}>
                {keyExists ? '✓ this PC has an encryption key' : 'no encryption key yet — generate one'}
              </span>
              {privateKeyPem && <span className="tag ok">🔑 private key loaded</span>}
            </div>
            {genErr && <div className="tag bad big">{genErr.slice(0, 160)}</div>}
            <p className="hint">
              <strong>Generate keypair</strong> makes a fresh RSA-3072 keypair: the server keeps only
              the <em>public</em> half (it can encrypt, never decrypt) and your browser immediately
              downloads the <em>private</em> half as <code>private_key.pem</code> — move it to a USB
              stick, it is never saved on this PC. <strong>load private_key.pem</strong> feeds that
              file back in when you want to unlock. A browser page cannot actually verify a file came
              from a USB device — nothing enforces that you keep it there, only your own habit does.
              What the page <em>does</em> verify cryptographically: whether this is the right key
              (a wrong or missing key is always rejected).
            </p>
          </section>

          <section className="panel">
            <h2>💾 Save (encrypt &amp; store on this PC)</h2>
            <button className="btn primary" onClick={save} disabled={saveStatus === 'running'}>
              {saveStatus === 'running' ? 'Saving…' : '💾 Save to this PC'}
            </button>
            {saveStatus === 'done' && lastPkg && (
              <div className="pkg-shape mono">
                <span>💾 stored on this PC{savedAt ? ` · ${new Date(savedAt).toLocaleTimeString()}` : ''}</span>
                <span>version {lastPkg.version} · nonce {String(lastPkg.nonce_b64).slice(0, 8)}…</span>
                <span>secret on disk: NO — RSA-wrapped session key only</span>
              </div>
            )}
            {saveErr && <div className="tag bad big">{saveErr.slice(0, 160)}</div>}
            <PipelineLog events={saveLog} />
          </section>

          <section className="panel">
            <h2>🔓 Unlock (read the saved diary)</h2>
            <button className="btn" onClick={unlock} disabled={unlockStatus === 'running' || !hasStored}>
              {unlockStatus === 'running' ? 'Unlocking…' : hasStored ? '🔓 Unlock with this key' : 'nothing saved yet'}
            </button>
            {unlockStatus === 'done' && unlocked && (
              <div className="tag ok big">✓ AUTHENTICATED — diary released into the editor above</div>
            )}
            {unlockErr && <div className="tag bad big">{unlockErr.slice(0, 160)}</div>}
            <PipelineLog events={unlockLog} />
          </section>

          <section className="panel">
            <button className="btn ghost" onClick={() => setAdvancedOpen((v) => !v)}>
              {advancedOpen ? '▾' : '▸'} advanced: backup / restore
            </button>
            {advancedOpen && (
              <div style={{ marginTop: 8 }}>
                <button
                  className="btn ghost"
                  disabled={!lastPkg}
                  onClick={() => lastPkg && downloadPackage(lastPkg, `diary-backup-${new Date().toISOString().replace(/[:.]/g, '-')}.pkg`)}
                >
                  ⬇ export a backup copy (.pkg)
                </button>{' '}
                <button className="btn ghost" onClick={() => importRef.current?.click()}>
                  ⬆ import a backup .pkg
                </button>
                <input ref={importRef} type="file" accept=".pkg,.json,application/json" hidden onChange={onImportPackage} />
                <p className="hint">
                  Import decrypts an exported backup with the loaded private key and drops it into the
                  editor above — click Save afterwards to make it the diary stored on this PC.
                </p>
                {importErr && <div className="tag big">{importErr.slice(0, 200)}</div>}
              </div>
            )}
          </section>
        </div>
      </div>

      <footer className="foot">
        <span className="tag">the fish is audit metadata → your RSA key holds the secret</span>
        <span className="hint">v4 packages store no secret on disk: an RSA-wrapped session key only — losing the private key means the entry is genuinely unrecoverable.</span>
      </footer>
    </div>
  )
}
