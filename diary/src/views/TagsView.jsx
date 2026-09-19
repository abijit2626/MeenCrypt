import { useState } from 'react'
import { MOODS } from '../lib/payload'
import { useDiary } from '../vault/DiaryContext'
import EntryGrid from './EntryGrid'
import LockedNotice from './LockedNotice'

export default function TagsView({ entries, onOpen, renderActions, onRequestUnlock }) {
  const { unlocked, decrypted } = useDiary()
  const [selected, setSelected] = useState(null)
  if (!unlocked) return <><h2 className="section-title">Tags</h2><LockedNotice what="Mood tagging" onRequestUnlock={onRequestUnlock} /></>

  const counts = new Map()
  for (const entry of entries) {
    const mood = decrypted.get(entry.id)?.mood
    if (mood) counts.set(mood, (counts.get(mood) || 0) + 1)
  }
  const shown = selected ? entries.filter((e) => decrypted.get(e.id)?.mood === selected) : []

  return (
    <>
      <h2 className="section-title">Tags</h2>
      <div className="mood-picker">
        {MOODS.filter((m) => counts.has(m.id)).map((mood) => (
          <button
            key={mood.id}
            className={`mood-chip ${selected === mood.id ? 'active' : ''}`}
            onClick={() => setSelected(selected === mood.id ? null : mood.id)}
          >
            {mood.emoji} {mood.label} <span className="mono dim">{counts.get(mood.id)}</span>
          </button>
        ))}
        {!counts.size && <p className="empty">No moods tagged yet — pick one in the composer.</p>}
      </div>
      {selected && <EntryGrid entries={shown} onOpen={onOpen} renderActions={renderActions} empty="No entries." />}
    </>
  )
}
