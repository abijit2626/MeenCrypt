import Icon from './Icon'

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
    body: 'The operating system\'s cryptographically secure random generator provides a fresh 32-byte session secret for every message. This is the actual source of secrecy - and it is discarded the moment it has been wrapped in step 6.',
    real: 'secrets.token_bytes()',
  },
  {
    n: '5',
    title: 'HKDF-SHA256 — cryptographic mixing',
    body: 'Fish digest + the fresh OS secret are combined with a standard KDF, using the explicit info string "FISHRAND-AES256-GCM-v4-rsa-hybrid" so the material can never be reused for another purpose.',
    real: 'domain-separated key derivation',
  },
  {
    n: '6',
    title: 'AES-256-GCM + RSA-OAEP — encrypt, then wrap the key',
    body: 'A fresh 12-byte nonce and the 256-bit key encrypt the diary (AAD is bound to the ciphertext). The session key is then wrapped with your RSA-3072 public key - only the matching private key can ever unwrap it.',
    real: 'no ECB · tamperproof tag · asymmetric key wrap',
  },
  {
    n: '7',
    title: 'Package — no secret stored, ever',
    body: 'A self-contained JSON envelope holds ciphertext + tag, nonce, AAD, and the RSA-wrapped session key. Decrypting needs ONLY your RSA private key - the fish window is never needed again.',
    real: 'losing the private key means the entry is unrecoverable, by design',
  },
]

export default function CryptoExplainer() {
  return (
    <section className="panel explainer">
      <div className="panel-head">
        <h2><Icon name="info" /> How the encryption works</h2>
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
      <p className="hint">
        The fish looks busy, but the real security comes from the operating
        system. That's the honest part of this project.
      </p>
    </section>
  )
}