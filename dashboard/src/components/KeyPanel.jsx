export default function KeyPanel({ derived = false }) {
  return (
    <section className="panel key-panel">
      <h2>🔑 4 · Key derivation</h2>
      <div className="keyrow"><span>KEY DERIVATION</span>
        <strong className={`tag ${derived ? 'ok' : ''}`}>{derived ? 'COMPLETE' : 'PENDING'}</strong></div>
      <div className="keyrow"><span>KEY SIZE</span><strong className="mono">256 bits (AES) · 3072 bits (RSA)</strong></div>
      <div className="keyrow"><span>KDF</span><strong className="mono">HKDF-SHA256</strong></div>
      <div className="keyrow"><span>INFO</span><strong className="mono">FISHRAND-AES256-GCM-v4-rsa-hybrid</strong></div>
      <div className="keyrow"><span>KEY WRAP</span><strong className="mono">RSA-OAEP-SHA256</strong></div>
      <div className="keyrow"><span>KEY MATERIAL</span><strong className="mono hidden">HIDDEN</strong></div>
      <div className="keyrow"><span>NONCE</span><strong className="mono">fresh 12 B / session</strong></div>
    </section>
  )
}