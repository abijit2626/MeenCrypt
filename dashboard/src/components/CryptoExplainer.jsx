const STAGES = [
  {
    n: '1',
    title: 'Input contract — validate the fish',
    body: 'The observation JSON is untrusted. Every frame must match a strict, versioned schema: numeric coordinates, sane ranges, proper timestamps, no NaN or infinity. Anything else is rejected before it can touch the crypto.',
    real: 'untrusted-input handling',
  },
  {
    n: '2',
    title: 'Canonicalization — same fish, same bytes',
    body: 'The same physical dataset produces exactly the same byte stream regardless of JSON formatting. Key order, number formatting and whitespace are pinned down, so hashing is deterministic.',
    real: 'deterministic serialization',
  },
  {
    n: '3',
    title: 'SHA-256 conditioning → fish_digest',
    body: 'The whole observation stream is compressed into a fixed 32-byte digest. This is conditioning, not magic: it does NOT create entropy from nothing.',
    real: 'the fish adds flavor, not security',
  },
  {
    n: '4',
    title: 'OS CSPRNG — the trusted randomness',
    body: 'The operating system\'s cryptographically secure random generator provides 32 bytes. This is the actual source of secrecy in the whole system.',
    real: 'secrets.token_bytes()',
  },
  {
    n: '5',
    title: 'HKDF-SHA256 — cryptographic mixing',
    body: 'Fish digest + OS randomness are combined with a standard KDF, using the explicit info string "FISHRAND-AES256-GCM-v1" so the material can never be reused for another purpose.',
    real: 'domain-separated key derivation',
  },
  {
    n: '6',
    title: 'AES-256-GCM — encrypt + authenticate',
    body: 'A fresh 12-byte nonce and the 256-bit key encrypt the diary. AAD (authenticated metadata) is bound to the ciphertext. Confidentiality, integrity and authentication in one pass.',
    real: 'no ECB · tamperproof tag',
  },
  {
    n: '7',
    title: 'Package — bound to the fish window',
    body: 'A self-contained JSON envelope holds ciphertext + tag, nonce, AAD and the fish window that produced the key. The secret AES key is never stored anywhere.',
    real: 'decrypt re-derives the key from this + the OS secret',
  },
]

export default function CryptoExplainer() {
  return (
    <section className="panel explainer">
      <div className="panel-head">
        <h2>🧠 How the encryption works</h2>
      </div>
      {STAGES.map((s) => (
        <div className="explain" key={s.n}>
          <span className="explain-n mono">{s.n}</span>
          <div>
            <strong>{s.title}</strong>
            <p>{s.body}</p>
            <span className="tag real">{s.real}</span>
          </div>
        </div>
      ))}
      <p className="hint" style={{ marginTop: 10 }}>
        The fish looks busy, but the real security comes from the operating
        system. That's the honest part of this project.
      </p>
    </section>
  )
}