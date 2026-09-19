// Short public fingerprint of an entry's RSA-wrapped session key. The
// wrapped key is ciphertext, so this is non-secret - it just shows at a
// glance that every entry got its own fresh key.

const cache = new Map()

export async function keyFingerprint(pkg) {
  const wrapped = pkg?.encrypted_session_key_b64
  if (!wrapped) return null
  if (cache.has(wrapped)) return cache.get(wrapped)
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(wrapped))
  const hex = Array.from(new Uint8Array(digest).slice(0, 4), (b) => b.toString(16).padStart(2, '0')).join('')
  cache.set(wrapped, hex)
  return hex
}
