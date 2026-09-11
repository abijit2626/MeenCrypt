import { useEffect, useRef } from 'react'

const STEPS = ['validation', 'canonicalization', 'conditioning', 'os_csprng', 'fish_chain', 'kdf', 'fish_commit', 'aes_gcm', 'package', 'decrypt']

const LABELS = {
  validation: 'Input contract',
  canonicalization: 'Canonicalization',
  conditioning: 'SHA-256 conditioning',
  os_csprng: 'OS CSPRNG / USB code',
  fish_chain: 'Fish-chain key schedule',
  kdf: 'HKDF key derivation',
  fish_commit: 'Fish commitment verify',
  aes_gcm: 'AES-256-GCM',
  package: 'Encrypted package',
  decrypt: 'Decryption',
}

export default function Pipeline({ events, mode, onRun, busy }) {
  const scrollRef = useRef(null)

  useEffect(() => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [events.length])

  const done = events.length === 0 ? null : STEPS
    .map((s) => events.filter((e) => e.step === s && e.status !== 'error'))
    .filter((arr) => arr.length > 0)
  const errored = new Set(events.filter((e) => e.status === 'error').map((e) => e.step))

  return (
    <section className="panel pipeline-panel">
      <div className="panel-head">
        <h2>⚙️ 2 · Pipeline ({mode === 'decrypt' ? 'decrypt' : 'encrypt'})</h2>
        <div className="panel-actions">
          {onRun && (
            <button className="btn primary" onClick={onRun} disabled={busy}>
              {busy ? 'Running…' : '▶ Encrypt current window'}
            </button>
          )}
        </div>
      </div>
      <div className="pipeline-scroll" ref={scrollRef}>
        {events.length === 0 && (
          <div className="empty">Pipeline events will stream here live.</div>
        )}
        {events.map((evt, i) => (
          <StepCard key={i} event={evt} errored={errored.has(evt.step)} />
        ))}
      </div>
      {done && done.length > 0 && (
        <svg className="flow-connector" height={40} viewBox="0 0 100 40" preserveAspectRatio="none">
          {done.slice(0, -1).map((_, i) => (
            <line key={i} x1={i * 100 / done.length + 20} y1="20" x2={(i + 1) * 100 / done.length + 20} y2="20"
              strokeOpacity={0.25} strokeWidth="2" style={{ filter: 'drop-shadow(0 0 6px rgba(0,255,136,.7))' }} />
          ))}
        </svg>
      )}
    </section>
  )
}

function StepCard({ event, errored }) {
  const status = event.status
  const ok = status === 'ok' || status === 'complete'
  const running = status === 'running'
  const detail = event.detail || {}
  return (
    <div className={`step ${ok ? 'ok' : ''} ${running ? 'run' : ''} ${errored || status === 'error' ? 'bad' : ''}`}>
      <div className="step-line">
        <span className="step-dot" />
        <span className="step-label">{(LABELS[event.step] || event.step).toUpperCase()}</span>
        <span className={`step-status ${ok ? 'ok' : running ? 'run' : 'bad'}`}>
          {status === 'error' ? 'AUTH FAILED' : status.toUpperCase()}
        </span>
        {event.duration_ms != null && <span className="step-dur mono">{event.duration_ms} ms</span>}
      </div>
      {Object.entries(detail).map(([k, v]) => (
        <div className="step-detail mono" key={k}>
          <span>{k}:</span>
          <span className={k === 'key_material' || k === 'secret' ? 'hidden' : ''}>{String(v)}</span>
        </div>
      ))}
    </div>
  )
}