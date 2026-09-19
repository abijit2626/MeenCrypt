import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchAudio, fetchDrives, fetchObservation, savePrivateKeyPem } from '../api'
import { clearDraft, loadDraft, saveDraft } from '../lib/drafts'
import { useDiary } from '../vault/DiaryContext'
import Icon from './Icon'
import Markdown from './Markdown'
import MoodPicker from './MoodPicker'

const POLL_MS = 3000
const CLOSE_ANIMATION_MS = 220

// Watches the server's live tank window so the writer can see what will
// condition this entry's key. The digest itself is computed server-side
// from the window current at the moment of saving.
function useTankStatus() {
  const [status, setStatus] = useState({ state: 'watching' })
  useEffect(() => {
    let live = true
    let timer = null
    async function poll() {
      try {
        const [obs, audio] = await Promise.all([fetchObservation(), fetchAudio().catch(() => null)])
        if (!live) return
        if (obs) {
          const meta = obs.metadata || {}
          setStatus({
            state: 'ready',
            units: meta.frame_count ?? meta.sample_count ?? null,
            fish: meta.max_fish_count,
            source: obs.source,
            audio: !!audio,
          })
        } else {
          setStatus({ state: 'none' })
        }
      } catch (err) {
        if (live) setStatus({ state: 'error', message: String(err.message || err) })
      }
      if (live) timer = setTimeout(poll, POLL_MS)
    }
    poll()
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [])
  return status
}

// Every entry must be tied to a physical drive on demand - never picked
// automatically, even when only one is detected (see the design plan).
function useDrives() {
  const [drives, setDrives] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const refresh = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      setDrives(await fetchDrives())
    } catch (err) {
      setError(String(err.message || err))
    } finally {
      setLoading(false)
    }
  }, [])

  // oxlint-disable-next-line react/set-state-in-effect
  useEffect(() => { refresh() }, [refresh])
  return { drives, loading, error, refresh }
}

function DrivePicker({ driveSerial, onPick, disabled }) {
  const { drives, loading, error, refresh } = useDrives()
  return (
    <div className="alert info">
      <div className="row">
        <strong><Icon name="plug" size={14} /> Drive for this entry’s key</strong>
        <span className="spacer" />
        <button type="button" className="btn ghost small" onClick={refresh} disabled={loading || disabled}>
          <Icon name="refresh" size={12} /> Refresh drives
        </button>
      </div>
      <p className="muted small">
        This entry can only be opened later with BOTH its key file and this exact physical drive present.
      </p>
      {error && <div className="alert bad">{error.slice(0, 200)}</div>}
      {!error && loading && <p className="muted small">Looking for drives…</p>}
      {!error && !loading && !drives.length && <p className="muted small">No drives detected — plug one in and refresh.</p>}
      {drives.map((d) => (
        <label key={d.serial} className="switch-row">
          <input
            type="radio"
            name="drive-serial"
            checked={driveSerial === d.serial}
            onChange={() => onPick(d)}
            disabled={disabled}
          />
          <span>
            <strong>{d.label}</strong>
            <small>{d.mountpoint}</small>
          </span>
        </label>
      ))}
    </div>
  )
}

function TankStatusLine({ status }) {
  if (status.state === 'watching') {
    return <span className="status-line watching"><i className="pulse" />Reading tank observation for key conditioning… watching</span>
  }
  if (status.state === 'ready') {
    const bits = [
      status.units != null && `${status.units} frames`,
      status.fish != null && `${status.fish} fish`,
      status.audio ? 'mic window ready' : 'no mic window',
    ].filter(Boolean)
    return <span className="status-line ready"><i className="pulse" />Reading tank observation for key conditioning… digest ready · {bits.join(' · ')}</span>
  }
  if (status.state === 'none') {
    return <span className="status-line warn"><i className="pulse" />No live tank window yet — the server will collect one when you save</span>
  }
  return <span className="status-line bad"><i className="pulse" />Can’t reach the tank server: {status.message}</span>
}

export default function Composer({ onClose }) {
  const { saveEntry } = useDiary()
  const [body, setBody] = useState('')
  const [mood, setMood] = useState(null)
  const [tab, setTab] = useState('write')
  const [closing, setClosing] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveStep, setSaveStep] = useState('')
  const [error, setError] = useState('')
  // Set once the entry is already encrypted and stored, waiting for its
  // one-of-a-kind private key to actually land on disk somewhere. There is
  // no copy of it anywhere else - closing this without saving it means the
  // entry can never be opened again.
  const [pendingKey, setPendingKey] = useState(null)
  const [keySaved, setKeySaved] = useState(false)
  const [keyBusy, setKeyBusy] = useState(false)
  const [keyError, setKeyError] = useState('')
  const [autosave, setAutosave] = useState(false)
  const [storedDraft, setStoredDraft] = useState(null)
  const [driveSerial, setDriveSerial] = useState(null)
  const [driveLabel, setDriveLabel] = useState('')
  const textRef = useRef(null)
  const status = useTankStatus()

  useEffect(() => {
    loadDraft().then((draft) => draft?.body && setStoredDraft(draft))
    textRef.current?.focus()
  }, [])

  // Debounced plaintext draft autosave - only while the toggle is on.
  useEffect(() => {
    if (!autosave) return
    const timer = setTimeout(() => {
      if (body.trim()) saveDraft({ body, mood }).catch(() => {})
    }, 800)
    return () => clearTimeout(timer)
  }, [autosave, body, mood])

  function close(force = false) {
    if (!force && pendingKey && !window.confirm(
      'This entry is already encrypted and saved, but its key is NOT saved anywhere yet. ' +
      'Closing now means this entry can never be opened again. Close anyway?',
    )) return
    if (!force && !pendingKey && body.trim() && !autosave && !window.confirm('Discard this unsaved entry?')) return
    setClosing(true)
    setTimeout(onClose, CLOSE_ANIMATION_MS)
  }

  function toggleAutosave(on) {
    setAutosave(on)
    if (!on) clearDraft()
  }

  function restoreDraft() {
    setBody(storedDraft.body)
    setMood(storedDraft.mood || null)
    setAutosave(true) // it was on when this draft was written
    setStoredDraft(null)
  }

  function discardDraft() {
    clearDraft()
    setStoredDraft(null)
  }

  const ready = status.state === 'ready' || status.state === 'none'
  const canSave = !!body.trim() && !!driveSerial && ready && !saving

  async function submit() {
    if (!canSave) return
    setSaving(true)
    setError('')
    try {
      const entry = await saveEntry({ body, mood }, driveSerial, (evt) => setSaveStep(String(evt.step)))
      await clearDraft()
      if (entry.privateKeyPem) {
        // Encrypted and stored already - now it's just about not losing
        // the only copy of the key that opens it.
        setPendingKey(entry)
        setSaving(false)
      } else {
        close(true)
      }
    } catch (err) {
      setError(String(err.message || err))
      setSaving(false)
    }
  }

  async function saveKey() {
    setKeyBusy(true)
    setKeyError('')
    try {
      await savePrivateKeyPem(pendingKey.privateKeyPem, `entry-${pendingKey.id}.pem`)
      setKeySaved(true)
    } catch (err) {
      if (err?.name !== 'AbortError') setKeyError(String(err.message || err))
    } finally {
      setKeyBusy(false)
    }
  }

  function copyKey() {
    navigator.clipboard?.writeText(pendingKey.privateKeyPem).catch(() => {})
  }

  if (pendingKey) {
    return (
      <div className={`composer ${closing ? 'closing' : ''}`} role="dialog" aria-label="Save this entry's key">
        <header className="composer-bar">
          <button className="icon-btn" onClick={() => close()} aria-label="Close composer"><Icon name="close" size={13} /></button>
          <div className="composer-title"><span>Save this entry’s key</span></div>
        </header>
        <div className="composer-body">
          {keySaved ? (
            <>
              <div className="alert ok">
                ✓ Key saved. This entry is safe — you can open it later from wherever you just saved that file.
              </div>
              <button className="btn primary" onClick={() => close(true)}>Done</button>
            </>
          ) : (
            <>
              <div className="alert warn">
                This entry is already encrypted and stored, but it has its own private key that exists ONLY right
                here, right now. Save it to <strong>{driveLabel || 'the drive you picked'}</strong> — if you save it
                somewhere else, this entry needs both the key file AND that exact drive to open again, so keep them
                together. Leave without saving it at all and this entry can never be opened.
              </div>
              <div className="row">
                <button className="btn primary" onClick={saveKey} disabled={keyBusy}>
                  <Icon name="save" size={13} /> {keyBusy ? 'Saving…' : 'Save As…'}
                </button>
                <button type="button" className="btn ghost" onClick={copyKey}>Copy to clipboard</button>
              </div>
              {keyError && <div className="alert bad">{keyError.slice(0, 240)}</div>}
              <p className="muted small">
                If the Save dialog doesn’t work, copy the text below and paste it into a <code>.pem</code> file on
                your USB drive yourself.
              </p>
              <textarea
                className="pem-input mono"
                rows={6}
                readOnly
                value={pendingKey.privateKeyPem}
                onFocus={(e) => e.target.select()}
                spellCheck={false}
              />
            </>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className={`composer ${closing ? 'closing' : ''}`} role="dialog" aria-label="New entry">
      <header className="composer-bar">
        <button className="icon-btn" onClick={() => close()} aria-label="Close composer" disabled={saving}><Icon name="close" size={13} /></button>
        <div className="composer-title">
          <span>New entry</span>
          <TankStatusLine status={status} />
        </div>
        <button className="btn primary" onClick={submit} disabled={!canSave}>
          {saving ? `Encrypting… ${saveStep}` : 'Encrypt & save'}
        </button>
      </header>

      <div className="composer-body">
        {storedDraft && (
          <div className="alert warn row">
            <span>A plaintext draft from {new Date(storedDraft.updatedAt).toLocaleString()} is stored in this browser.</span>
            <span className="spacer" />
            <button className="btn ghost small" onClick={restoreDraft}>Restore</button>
            <button className="btn ghost small" onClick={discardDraft}>Discard</button>
          </div>
        )}

        {autosave && (
          <div className="alert warn">
            <Icon name="warning" size={13} /> Draft autosave is on: this draft is stored as <strong>PLAINTEXT</strong> in this browser (IndexedDB)
            until you Encrypt &amp; save it. Anyone with access to this browser profile can read it.
          </div>
        )}

        <DrivePicker
          driveSerial={driveSerial}
          disabled={saving}
          onPick={(d) => { setDriveSerial(d.serial); setDriveLabel(`${d.label} — ${d.mountpoint}`) }}
        />

        <MoodPicker value={mood} onChange={setMood} />

        <div className="tabs">
          <button className={tab === 'write' ? 'active' : ''} onClick={() => setTab('write')}>Write</button>
          <button className={tab === 'preview' ? 'active' : ''} onClick={() => setTab('preview')}>Preview</button>
          <span className="spacer" />
          <span className="muted small">Markdown supported: **bold**, _italic_, # headings, - lists, `code`</span>
        </div>

        {tab === 'write' ? (
          <textarea
            ref={textRef}
            className="composer-text"
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="Dear future me…"
            spellCheck
          />
        ) : (
          <div className="composer-preview">
            {body.trim() ? <Markdown>{body}</Markdown> : <p className="muted">Nothing to preview yet.</p>}
          </div>
        )}

        <label className="switch-row">
          <input type="checkbox" checked={autosave} onChange={(e) => toggleAutosave(e.target.checked)} />
          <span>
            <strong>Autosave draft in this browser</strong>
            <small>Off by default. Drafts are unencrypted until saved; turning this off deletes the stored draft.</small>
          </span>
        </label>

        {error && <div className="alert bad">{error.slice(0, 240)}</div>}
      </div>
    </div>
  )
}
