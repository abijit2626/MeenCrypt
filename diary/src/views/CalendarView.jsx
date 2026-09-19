import Heatmap from '../components/Heatmap'
import { currentStreak, longestStreak } from '../lib/calendar'

export default function CalendarView({ entries, onSelectDay }) {
  return (
    <>
      <h2 className="section-title">Calendar</h2>
      <section className="panel">
        <div className="stat-row">
          <div className="stat"><span className="stat-num">{currentStreak(entries)}</span><span>current streak</span></div>
          <div className="stat"><span className="stat-num">{longestStreak(entries)}</span><span>longest streak</span></div>
          <div className="stat"><span className="stat-num">{entries.length}</span><span>entries</span></div>
        </div>
        <Heatmap entries={entries} days={364} large onSelectDay={onSelectDay} />
        <p className="muted small">
          Built from each package’s plaintext date and tank-quality metadata. Nothing is decrypted to draw this.
        </p>
      </section>
    </>
  )
}
