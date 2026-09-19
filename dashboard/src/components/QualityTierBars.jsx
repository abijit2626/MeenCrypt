import Icon from './Icon'

const TIERS = [
  ['GOOD', 'good'],
  ['MEDIUM', 'medium'],
  ['BAD', 'bad'],
]

// Panel 4/7: how the recent fish-poll window classifies (fishrand/quality.py),
// not an all-time count - see server/metrics.py's record_fish_sample.
export default function QualityTierBars({ metrics }) {
  const tiers = metrics.quality_tiers
  const total = tiers.GOOD + tiers.MEDIUM + tiers.BAD || 1

  return (
    <section className="panel">
      <div className="panel-head">
        <h2><Icon name="target" /> Fish quality mix</h2>
      </div>
      {TIERS.map(([label, cls]) => {
        const count = tiers[label]
        const pct = Math.round((count / total) * 100)
        return (
          <div className="bar-row" key={label}>
            <span className="bar-label mono">{label}</span>
            <div className="bar-track">
              <div className={`bar-fill ${cls}`} style={{ width: `${pct}%` }} />
            </div>
            <span className="bar-count mono">{count}</span>
          </div>
        )
      })}
      <p className="hint">Last {metrics.fish_activity.length} poll ticks - GOOD/MEDIUM/BAD per fishrand/quality.py.</p>
    </section>
  )
}
