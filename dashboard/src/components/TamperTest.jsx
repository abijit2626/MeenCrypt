import { useRef, useState } from 'react'
import { streamPipeline } from '../api'

// Demo stunt: flip one bit in the ciphertext of the current package and
// show the GCM tag refusing to verify. Since v4 packages decrypt with the
// RSA private key alone (no fish/code needed), this needs the SAME private
// key private_key.pem that matches the server's current public key - the
// same one the diary app hands out from "Generate keypair". Loaded here
// only in memory for this one demo click, never sent anywhere but /api/decrypt.
export default function TamperTest({ disabled }) {
  const [result, setResult] = useState(null)
  const [privateKeyPem, setPrivateKeyPem] = useState('')
  const keyRef = useRef(null)

  function tamper(pkg) {
    const flip = (b64) => {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
      bytes[Math.floor(bytes.length / 2)] ^= 0x01
      // btoa on the array to re-encode
      return btoa(String.fromCharCode(...bytes))
    }
    return { ...pkg, payload_b64: flip(pkg.payload_b64) }
  }

  async function onLoadKey(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const text = await file.text()
    setPrivateKeyPem(text.trim())
    e.target.value = ''
  }

  const onTamper = async () => {
    setResult('running')
    try {
      // App stores the package in window.__fishPkg
      if (!window.__fishPkg) { setResult('no-package'); return }
      await streamPipeline('/api/decrypt', {
        package: tamper(window.__fishPkg),
        private_key_pem: privateKeyPem,
      }, () => {})
      setResult('unexpected-success')
    } catch {
      setResult('rejected')
    }
  }

  const noKey = !privateKeyPem.trim()

  return (
    <section className={`panel tamper-panel ${disabled ? 'dim' : ''}`}>
      <h2>🫀 5 · Tamper test</h2>
      <p className="hint">
        Flip one ciphertext bit, then watch the AES-256-GCM tag refuse to
        authenticate the tampered payload.
      </p>
      <div className="row-between">
        <button className="btn ghost" onClick={() => keyRef.current?.click()}>load private_key.pem</button>
        <input ref={keyRef} type="file" accept=".pem,.txt" hidden onChange={onLoadKey} />
        {privateKeyPem && <span className="tag ok">🔑 key loaded</span>}
      </div>
      <button onClick={onTamper} className="btn danger" disabled={disabled || noKey || result === 'running'}>
        {result === 'running' ? 'Tampering…' : 'Flip a bit & re-decrypt'}
      </button>
      {noKey && !disabled && (
        <p className="hint">
          Needs the RSA private key matching this server's public key (the one the
          diary app's "Generate keypair" downloads) - v4 packages have no fish/code
          fallback to decrypt with.
        </p>
      )}
      {result === 'rejected' && (
        <div className="tag bad big">AUTHENTICATION FAILED — tampered data rejected</div>
      )}
      {result === 'unexpected-success' && (
        <div className="tag bad big">⚠ tamper undetected (bug!)</div>
      )}
      {result === 'no-package' && (
        <div className="tag">Encrypt something first, then flip a bit here.</div>
      )}
    </section>
  )
}