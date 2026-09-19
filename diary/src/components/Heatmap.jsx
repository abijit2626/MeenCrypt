import { formatDate, heatmapDays } from '../lib/calendar'

// Metadata only: tier + pinned come from plaintext package metadata/flags.
export default function Heatmap({ entries, days = 35, onSelectDay, large = false }) {
  const cells = heatmapDays(entries, days)
  // Pad the front so columns line up with weekdays (Mon first).
  const lead = (cells[0].date.getDay() + 6) % 7
  return (
    <div className={`heatmap ${large ? 'large' : ''}`}>
      <div className="heatmap-grid">
        {['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((d, i) => <span key={`h${i}`} className="heatmap-dow">{d}</span>)}
        {Array.from({ length: lead }, (_, i) => <span key={`pad${i}`} />)}
        {cells.map((cell) => (
          <button
            key={cell.key}
            className={`heat-cell ${cell.tier ? cell.tier.toLowerCase() : 'empty'} ${cell.pinned ? 'pinned' : ''}`}
            disabled={!cell.count}
            onClick={() => onSelectDay(cell.key)}
            title={`${formatDate(cell.date)} — ${cell.count ? `${cell.count} ${cell.count === 1 ? 'entry' : 'entries'}${cell.tier ? `, ${cell.tier.toLowerCase()} tank` : ''}${cell.pinned ? ', pinned' : ''}` : 'no entry'}`}
          />
        ))}
      </div>
      <div className="heatmap-legend">
        <span><i className="heat-cell good" /> good</span>
        <span><i className="heat-cell medium" /> medium</span>
        <span><i className="heat-cell bad" /> bad</span>
        <span><i className="heat-cell empty pinned" /> pinned</span>
      </div>
    </div>
  )
}
