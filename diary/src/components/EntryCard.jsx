import { useEffect, useState } from 'react'
import { createdAt, formatDate, formatWeekday, dayKey, tierOf } from '../lib/calendar'
import { keyFingerprint } from '../lib/fingerprint'
import { moodById, stripMarkdown } from '../lib/payload'
import { useDiary } from '../vault/DiaryContext'
import Icon from './Icon'

const TIER_LABEL = { GOOD: 'good tank reading', MEDIUM: 'medium — fish + mic', BAD: 'bad — mic only' }

export function Fingerprint({ pkg }) {
  const [fp, setFp] = useState(null)
  useEffect(() => {
    let live = true
    keyFingerprint(pkg).then((hex) => live && setFp(hex))
    return () => { live = false }
  }, [pkg])
  return (
    <span className="fp mono" title="SHA-256 of this entry’s RSA-wrapped session key — public, just shows each entry got a fresh key">
      <Icon name="key" size={12} /> {fp || '········'}
    </span>
  )
}

export default function EntryCard({ entry, highlighted, onOpen, actions }) {
  const { decrypted, sealReasons } = useDiary()
  const content = decrypted.get(entry.id)
  const sealReason = sealReasons.get(entry.id)
  const needsDrive = !content && sealReason?.code === 'drive_missing'
  const date = createdAt(entry)
  const tier = tierOf(entry)
  const mood = content && moodById(content.mood)

  return (
    <article
      className={`card ${highlighted ? 'highlight' : ''} ${entry.flags?.pinned ? 'pinned' : ''}`}
      data-day={dayKey(date)}
      data-entry={entry.id}
      onClick={() => onOpen(entry)}
      tabIndex={0}
      onKeyDown={(e) => e.key === 'Enter' && onOpen(entry)}
    >
      <div className="card-head">
        <div>
          <div className="card-date">{formatDate(date)}</div>
          <div className="card-weekday">{formatWeekday(date)}</div>
        </div>
        <div className="card-badges">
          {mood && <span className="mood-tag" title={mood.label}>{mood.emoji} {mood.label}</span>}
          <span className={`quality-dot ${tier ? tier.toLowerCase() : 'none'}`} title={tier ? TIER_LABEL[tier] : 'no quality recorded'} />
        </div>
      </div>

      {content ? (
        <p className="card-snippet">{stripMarkdown(content.body).slice(0, 220) || <em className="muted">(empty)</em>}</p>
      ) : (
        <div className="card-sealed" aria-label="sealed entry" title={needsDrive ? sealReason.message : undefined}>
          <p className="blurred" aria-hidden>
            {needsDrive
              ? 'the right key was found, but this entry’s own drive isn’t plugged in'
              : 'the tank keeps this one sealed until your private key opens the vault again'}
          </p>
          <span className="sealed-label">
            <Icon name={needsDrive ? 'plug' : 'lockClosed'} size={12} /> {needsDrive ? 'needs its drive' : 'sealed'}
          </span>
        </div>
      )}

      <div className="card-foot">
        <Fingerprint pkg={entry.package} />
        <span className="spacer" />
        {actions && (
          <span className="card-actions" onClick={(e) => e.stopPropagation()}>
            {actions}
          </span>
        )}
        <span className="lock-icon" title={content ? 'decrypted in this tab' : (needsDrive ? sealReason.message : 'encrypted')}>
          <Icon name={content ? 'lockOpen' : needsDrive ? 'plug' : 'lockClosed'} size={13} />
        </span>
      </div>
    </article>
  )
}
