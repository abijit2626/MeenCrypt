import { useEffect, useRef, useState } from 'react'
import { downloadPackage, fetchDiaryState, parsePackageFile, readTextFile, streamPipeline } from './api'
import './App.css'

const FRESH_PLACEHOLDER = `Dear future me,

I hid the spare fish flakes behind the power strip.
Do not tell Fibonacci. He has a fragile ego.

- me, trusting a fish (and a USB key) with my secrets`

const CODE_HINT = 'your universal USB code (code.txt) — keep the ONLY copy on a USB stick'

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

  const [diary, setDiary] = useState('')
  const [code, setCode] = useState('')

  const [saveLog, setSaveLog] = useState([])
  const [unlockLog, setUnlockLog] = useState([])
  const [saveStatus, setSaveStatus] = useState('idle')     // idle|running|done|error
  const [unlockStatus, setUnlockStatus] = useState('idle')
  const [saveErr, setSaveErr] = useState('')
  const [unlockErr, setUnlockErr] = useState('')

  const [lastPkg, setLastPkg] = useState(null)              // for the backup-export button
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [importErr, setImportErr] = useState('')

  const codeRef = useRef(null)
  const importRef = useRef(null)

  async function refreshDiaryState() {
    setLoadingState(true)
    try {
      const state = await fetchDiaryState()
      setHasStored(!!state.exists)
      setSavedAt(state.saved_at || null)
      setLastPkg(state.exists ? state.package : null)
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

  async function onLoadCode(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const text = (await readTextFile(file)).trim()
    setCode(text)
    e.target.value = ''
  }

  async function save() {
    if (!diary.trim()) { setSaveErr('Write something first.'); return }
    if (!code.trim()) { setSaveErr('Load your universal USB code (code.txt) first.'); return }
    setSaveStatus('running')
    setSaveErr('')
    setSaveLog([])
    try {
      const payload = await streamPipeline(
        '/api/diary/save',
        { plaintext: diary, code: code.trim() },
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
    if (!code.trim()) { setUnlockErr('Load your universal USB code (code.txt) first.'); return }
    setUnlockStatus('running')
    setUnlockErr('')
    setUnlockLog([])
    try {
      const payload = await streamPipeline(
        '/api/diary/unlock',
        { code: code.trim() },
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
    if (!code.trim()) { setImportErr('Load your universal USB code (code.txt) first.'); return }
    setImportErr('')
    setUnlockStatus('running')
    setUnlockLog([])
    try {
      const imported = await parsePackageFile(file)
      const payload = await streamPipeline(
        '/api/decrypt',
        { package: imported, code: code.trim() },
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
          <h1>FISHRAND · DIARY</h1>
          <p className="sub">a diary kept encrypted on this PC — only your USB code opens it</p>
        </div>
        <nav className="nav">
          <span className="tag">save → stored encrypted, on this PC</span>
          <span className="tag">unlock → USB code required</span>
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
            className="mono diary-big"
            rows={20}
            spellCheck={false}
            placeholder={
              showLockedPlaceholder
                ? '🔒 A diary is saved here, encrypted. Load your USB code and click Unlock to read it — or just start typing to overwrite it with a fresh entry.'
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
              <h2>🔑 The universal USB code</h2>
              <div className="panel-actions">
                <button className="btn ghost" onClick={() => codeRef.current?.click()}>load code.txt</button>
                <input ref={codeRef} type="file" accept=".txt" hidden onChange={onLoadCode} />
              </div>
            </div>
            <textarea
              className="mono"
              rows={2}
              spellCheck={false}
              placeholder={CODE_HINT}
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            <p className="hint">
              generated once with <code>python cli.py init --dir /media/USB</code>.
              This one code unlocks <em>any</em> message the fish encrypts. A browser page
              cannot actually verify a file came from a USB device — nothing enforces that
              you keep it there, only your own habit does. What the page <em>does</em> verify
              cryptographically: whether this code is the right one (wrong code is always rejected).
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
                <span>secret on disk: {lastPkg.version === 1 ? 'YES (v1 demo — avoid for real use)' : 'NO (USB code only)'}</span>
              </div>
            )}
            {saveErr && <div className="tag bad big">{saveErr.slice(0, 160)}</div>}
            <PipelineLog events={saveLog} />
          </section>

          <section className="panel">
            <h2>🔓 Unlock (read the saved diary)</h2>
            <button className="btn" onClick={unlock} disabled={unlockStatus === 'running' || !hasStored}>
              {unlockStatus === 'running' ? 'Unlocking…' : hasStored ? '🔓 Unlock with this code' : 'nothing saved yet'}
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
                  Import decrypts an exported backup with the loaded USB code and drops it into the
                  editor above — click Save afterwards to make it the diary stored on this PC.
                </p>
                {importErr && <div className="tag big">{importErr.slice(0, 200)}</div>}
              </div>
            )}
          </section>
        </div>
      </div>

      <footer className="foot">
        <span className="tag">the fish signs the window → the USB code holds the secret</span>
        <span className="hint">v3 packages store no secret on disk: the fish chains the key, and code.txt holds the door.</span>
      </footer>
    </div>
  )
}
