import { useRef, useState } from 'react'
import { generateKeypair, parsePackageFile } from '../api'
import Icon from '../components/Icon'
import KeepUnlockedToggle from '../components/KeepUnlockedToggle'
import { useDiary } from '../vault/DiaryContext'

export default function VaultView({ onRequestUnlock }) {
  const { keyExists, refresh, unlocked, lock, entries, decrypted, decryptWithVaultKey, saveEntry } = useDiary()
  const [genBusy, setGenBusy] = useState(false)
  const [message, setMessage] = useState(null)
  const importRef = useRef(null)

  async function onGenerate() {
    if (keyExists && !window.confirm(
      'A key already exists. A new keypair makes every entry already saved on this PC permanently unreadable. Continue?',
    )) return
    setGenBusy(true)
    setMessage(null)
    try {
      await generateKeypair(keyExists)
      await refresh()
      setMessage({ kind: 'ok', text: 'New keypair made — private_key.pem downloaded. Move it somewhere safe (e.g. a USB stick).' })
    } catch (err) {
      setMessage({ kind: 'bad', text: String(err.message || err) })
    } finally {
      setGenBusy(false)
    }
  }

  async function onImport(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setMessage({ kind: 'info', text: `Importing ${file.name}…` })
    try {
      const content = await decryptWithVaultKey(await parsePackageFile(file))
      await saveEntry(content)
      setMessage({ kind: 'ok', text: `${file.name} decrypted and saved as a new entry (re-encrypted with a fresh tank window).` })
    } catch (err) {
      setMessage({ kind: 'bad', text: String(err.message || err) })
    }
  }

  return (
    <>
      <h2 className="section-title">Vault</h2>

      <section className="panel">
        <h3><Icon name="key" size={15} /> Encryption key</h3>
        <p className={`pill ${keyExists ? 'ok' : 'bad'}`}>
          {keyExists ? '✓ this PC has a public key to encrypt with' : 'no encryption key yet — generate one'}
        </p>
        <p className="muted">
          <strong>Generate keypair</strong> makes a fresh RSA-3072 keypair. The server keeps only the <em>public</em> half,
          so it can encrypt but never decrypt. Your browser downloads the <em>private</em> half as{' '}
          <code>private_key.pem</code>; it is never saved on this PC. Lose it and every entry is unrecoverable.
        </p>
        <button className="btn" onClick={onGenerate} disabled={genBusy}>
          <Icon name={keyExists ? 'refresh' : 'sparkle'} size={13} /> {genBusy ? 'Generating…' : keyExists ? 'Generate new keypair' : 'Generate keypair'}
        </button>
      </section>

      <section className="panel">
        <h3><Icon name={unlocked ? 'lockOpen' : 'lockClosed'} size={15} /> {unlocked ? 'Vault unlocked' : 'Vault locked'}</h3>
        <p className="muted">
          {unlocked
            ? `${decrypted.size} of ${entries.length} entries decrypted in this tab’s memory.`
            : `${entries.length} entries sealed. Snippets, moods, search and stats need the private key.`}
        </p>
        <KeepUnlockedToggle />
        <button className={`btn ${unlocked ? '' : 'primary'}`} onClick={unlocked ? lock : onRequestUnlock}>
          {unlocked ? 'Lock now' : 'Unlock…'}
        </button>
      </section>

      <section className="panel">
        <h3><Icon name="package" size={15} /> Backups</h3>
        <p className="muted">
          Export a single entry as a <code>.pkg</code> from its reader view. Import decrypts a <code>.pkg</code> with the
          unlocked key and saves it as a new entry.
        </p>
        <button className="btn ghost" onClick={() => importRef.current?.click()} disabled={!unlocked}>
          <Icon name="upload" size={13} /> Import a .pkg
        </button>
        <input ref={importRef} type="file" accept=".pkg,.json,application/json" hidden onChange={onImport} />
      </section>

      {message && <div className={`alert ${message.kind}`}>{message.text.slice(0, 240)}</div>}

      <p className="muted small">
        What’s visible without the key: dates, tank quality, pin/archive/bin flags and key fingerprints. Text and
        moods are only inside the ciphertext.
      </p>
    </>
  )
}
