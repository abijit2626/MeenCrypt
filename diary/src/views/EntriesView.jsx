import { useEffect, useState } from 'react'
import Heatmap from '../components/Heatmap'
import Icon from '../components/Icon'
import { currentStreak, createdAt, dayKey, formatDate, onThisDay } from '../lib/calendar'
import { moodById, stripMarkdown } from '../lib/payload'
import { useDiary } from '../vault/DiaryContext'
import EntryGrid from './EntryGrid'

// Scroll the first card written on `key` into view; returns its entry id.
function scrollToDay(key) {
  const target = document.querySelector(`[data-day="${key}"]`)
  if (!target) return null
  target.scrollIntoView({ behavior: 'smooth', block: 'center' })
  return target.dataset.entry
}

function OnThisDay({ entries, onOpen }) {
  const { decrypted } = useDiary()
  const matches = onThisDay(entries).filter((e) => decrypted.has(e.id))
  if (!matches.length) return null
  return (
    <section className="panel on-this-day">
      <h3><Icon name="calendar" size={14} /> On this day</h3>
      <div className="otd-list">
        {matches.map((entry) => {
          const content = decrypted.get(entry.id)
          const mood = moodById(content.mood)
          return (
            <button key={entry.id} className="otd-item" onClick={() => onOpen(entry)}>
              <span className="otd-date">{formatDate(createdAt(entry))}{mood && ` · ${mood.emoji}`}</span>
              <span className="otd-snippet">{stripMarkdown(content.body).slice(0, 140)}</span>
            </button>
          )
        })}
      </div>
    </section>
  )
}

export default function EntriesView({ entries, searching, focusDay, onFocusHandled, onOpen, renderActions }) {
  const { unlocked } = useDiary()
  const [highlightId, setHighlightId] = useState(null)
  const streak = currentStreak(entries)

  function selectDay(key) {
    const id = scrollToDay(key)
    if (id) setHighlightId(id)
  }

  useEffect(() => {
    if (!highlightId) return
    const timer = setTimeout(() => setHighlightId(null), 2000)
    return () => clearTimeout(timer)
  }, [highlightId])

  // Arriving from the Calendar page with a day to jump to.
  useEffect(() => {
    if (!focusDay) return
    // Wait a frame so the cards exist; clearing focusDay must not cancel it.
    requestAnimationFrame(() => {
      const id = scrollToDay(focusDay)
      if (id) setHighlightId(id)
    })
    onFocusHandled()
  }, [focusDay, onFocusHandled])

  if (searching) {
    return (
      <>
        <h2 className="section-title">Search results</h2>
        <EntryGrid entries={entries} onOpen={onOpen} renderActions={renderActions} empty="No unlocked entries match." />
      </>
    )
  }

  const pinned = entries.filter((e) => e.flags?.pinned)
  const rest = entries.filter((e) => !e.flags?.pinned)
  const todayKey = dayKey(new Date())
  const wroteToday = entries.some((e) => dayKey(createdAt(e)) === todayKey)

  return (
    <>
      {unlocked && <OnThisDay entries={entries} onOpen={onOpen} />}

      <section className="panel overview">
        <div>
          <h3>Tank log · last 35 days</h3>
          <Heatmap entries={entries} onSelectDay={selectDay} />
        </div>
        <div className="streak">
          <span className="streak-num">{streak}</span>
          <span className="streak-label">day streak</span>
          <span className="muted small">{wroteToday ? 'written today ✓' : streak ? 'write today to keep it going' : 'start one today'}</span>
        </div>
      </section>

      {pinned.length > 0 && (
        <>
          <h2 className="section-title"><Icon name="pin" size={14} /> Pinned</h2>
          <EntryGrid entries={pinned} highlightId={highlightId} onOpen={onOpen} renderActions={renderActions} />
        </>
      )}
      <h2 className="section-title">All entries</h2>
      <EntryGrid
        entries={rest}
        highlightId={highlightId}
        onOpen={onOpen}
        renderActions={renderActions}
        empty={pinned.length ? 'Everything is pinned.' : 'No entries yet — tap + to write the first one.'}
      />
    </>
  )
}
