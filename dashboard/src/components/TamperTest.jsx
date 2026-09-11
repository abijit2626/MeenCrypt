import { useState } from 'react'
import { streamPipeline } from '../api'

// Demo stunt: flip one bit in the ciphertext of the current package and
// show the GCM tag refusing to verify.
export default function TamperTest({ disabled }) {
  const [result, setResult] = useState(null)

  function tamper(pkg) {
    const flip = (b64) => {
      const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))
      bytes[Math.floor(bytes.length / 2)] ^= 0x01
      // btoa on the array to re-encode
      return btoa(String.fromCharCode(...bytes))
    }
    return { ...pkg, payload_b64: flip(pkg.payload_b64) }
  }

  const onTamper = async () => {
    setResult('running')
    try {
      // App stores the package in window.__fishPkg
      if (!window.__fishPkg) { setResult('no-package'); return }
      await streamPipeline('/api/decrypt', {
        fish_json: window.__fishJson,
        package: tamper(window.__fishPkg),
      }, () => {})
      setResult('unexpected-success')
    } catch {
      setResult('rejected')
    }
  }

  return (
    <section className={`panel tamper-panel ${disabled ? 'dim' : ''}`}>
      <h2>🫀 5 · Tamper test</h2>
      <p className="hint">
        Flip one ciphertext bit, then watch the AES-256-GCM tag refuse to
        authenticate the tampered payload.
      </p>
      <button onClick={onTamper} className="btn danger" disabled={disabled || result === 'running'}>
        {result === 'running' ? 'Tampering…' : 'Flip a bit & re-decrypt'}
      </button>
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