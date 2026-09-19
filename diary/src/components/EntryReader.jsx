import { downloadPackage } from '../api'
import { createdAt, formatDate, formatWeekday, tierOf } from '../lib/calendar'
import { moodById } from '../lib/payload'
import { useDiary } from '../vault/DiaryContext'
import { Fingerprint } from './EntryCard'
import Icon from './Icon'
import Markdown from './Markdown'

export default function EntryReader({ entry, onClose }) {
  const { decrypted } = useDiary()
  const content = decrypted.get(entry.id)
  const date = createdAt(entry)
  const mood = content && moodById(content.mood)
  const meta = entry.package?.metadata || {}

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal reader">
        <div className="modal-head">
          <div>
            <h2>{formatDate(date)}</h2>
            <div className="muted small">
              {formatWeekday(date)} · {date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              {mood && <> · {mood.emoji} {mood.label}</>}
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close"><Icon name="close" size={13} /></button>
        </div>
        {content ? <Markdown>{content.body}</Markdown> : <p className="muted"><Icon name="lockClosed" size={13} /> Still decrypting…</p>}
        <div className="reader-meta mono small">
          <Fingerprint pkg={entry.package} />
          <span>tank: {tierOf(entry)?.toLowerCase() || '—'}</span>
          <span>mode: {meta.observation_mode || '—'}</span>
          <span>{entry.package?.algorithm} · {entry.package?.key_algorithm}</span>
          <button
            className="btn ghost small"
            onClick={() => downloadPackage(entry.package, `meencrypt-${entry.id}.pkg`)}
          >
            <Icon name="download" size={12} /> export .pkg
          </button>
        </div>
      </div>
    </div>
  )
}
