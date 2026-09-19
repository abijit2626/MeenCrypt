import { useEffect, useState } from 'react'
import { fetchVaultStatus, subscribeVaultStatus } from '../api'
import Icon from './Icon'

// Panel 3/7: LOCKED/UNLOCKED, flipped live by the diary app's own
// lock()/unlock() calls to POST /api/vault/status - see
// diary/src/vault/DiaryContext.jsx. Cosmetic only: a plain boolean, never
// the key or any decrypted content (server/main.py's module docstring).
export default function VaultStateStat() {
  const [status, setStatus] = useState(null) // {unlocked, updated_at}

  useEffect(() => {
    let cancelled = false
    fetchVaultStatus().then((s) => { if (!cancelled) setStatus(s) }).catch(() => {})
    const es = subscribeVaultStatus((s) => setStatus(s))
    return () => {
      cancelled = true
      es.close()
    }
  }, [])

  const unlocked = !!status?.unlocked
  return (
    <section className="panel">
      <div className="panel-head">
        <h2><Icon name="vault" /> Vault state</h2>
      </div>
      <div className="stat-big">
        <span className={`stat-big-value ${unlocked ? 'ok' : 'bad'}`}>
          <Icon name={unlocked ? 'lockOpen' : 'lockClosed'} size={22} /> {unlocked ? 'UNLOCKED' : 'LOCKED'}
        </span>
        <span className="hint mono">
          {status?.updated_at ? new Date(status.updated_at).toLocaleTimeString() : 'no diary session seen yet'}
        </span>
      </div>
    </section>
  )
}
