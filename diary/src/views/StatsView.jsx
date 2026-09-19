import { useMemo } from 'react'
import { formatDate } from '../lib/calendar'
import { computeStats } from '../lib/stats'
import { useDiary } from '../vault/DiaryContext'
import LockedNotice from './LockedNotice'

export default function StatsView({ entries, onOpenId, onRequestUnlock }) {
  const { unlocked, decrypted } = useDiary()
  // Recomputed in memory on every visit; never cached or stored.
  const stats = useMemo(() => computeStats(
    entries.filter((e) => decrypted.has(e.id)).map((entry) => ({ entry, content: decrypted.get(entry.id) })),
  ), [entries, decrypted])

  if (!unlocked) return <><h2 className="section-title">Stats</h2><LockedNotice what="Stats" onRequestUnlock={onRequestUnlock} /></>

  return (
    <>
      <h2 className="section-title">Stats</h2>
      <p className="muted small">Calculated from this session’s decrypted entries. Nothing is saved; it disappears when the vault locks.</p>
      <section className="panel stat-row">
        <div className="stat"><span className="stat-num">{stats.entries}</span><span>entries</span></div>
        <div className="stat"><span className="stat-num">{stats.totalWords.toLocaleString()}</span><span>words written</span></div>
        <div className="stat"><span className="stat-num">{stats.averageWords}</span><span>words per entry</span></div>
        <div className="stat"><span className="stat-num">{stats.activeHour}</span><span>most active hour</span></div>
      </section>

      {stats.longest && (
        <button className="panel longest" onClick={() => onOpenId(stats.longest.id)}>
          <h3>Longest entry · {stats.longest.words.toLocaleString()} words</h3>
          <span className="muted small">{formatDate(stats.longest.date)}</span>
          <p>{stats.longest.snippet}…</p>
        </button>
      )}

      <h2 className="section-title">Wrapped</h2>
      <div className="grid">
        {stats.wrapped.map((year) => (
          <section key={year.year} className="panel wrapped">
            <span className="wrapped-year">{year.year}</span>
            <ul>
              <li><strong>{year.entries}</strong> entries</li>
              <li><strong>{year.words.toLocaleString()}</strong> words</li>
              <li>busiest month: <strong>{year.bestMonth}</strong></li>
              <li>longest streak: <strong>{year.longestStreak}</strong> days</li>
              <li>top mood: <strong>{year.topMood ? `${year.topMood.emoji} ${year.topMood.label}` : '—'}</strong></li>
            </ul>
          </section>
        ))}
      </div>
    </>
  )
}
