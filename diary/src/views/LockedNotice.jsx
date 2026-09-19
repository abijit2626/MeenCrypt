import Icon from '../components/Icon'

export default function LockedNotice({ what, onRequestUnlock }) {
  return (
    <section className="panel locked-notice">
      <span className="big-icon" aria-hidden><Icon name="lockClosed" size={40} /></span>
      <p>{what} is worked out from decrypted entries, so it only exists while the vault is unlocked.</p>
      <button className="btn primary" onClick={onRequestUnlock}>Unlock vault</button>
    </section>
  )
}
