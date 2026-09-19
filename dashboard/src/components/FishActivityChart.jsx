import { linePoints } from '../lib/chart'
import Icon from './Icon'

const W = 260
const H = 64

// Panel 1/7: fish activity over time (metrics.fish_activity, fed by the
// server's fish-poll loop every tick - see server/main.py).
export default function FishActivityChart({ metrics }) {
  const samples = metrics.fish_activity
  const values = samples.map((s) => s.activity_pct)
  const points = linePoints(values, { width: W, height: H, min: 0 })
  const latest = samples[samples.length - 1]

  return (
    <section className="panel">
      <div className="panel-head">
        <h2><Icon name="trendingUp" /> Fish activity</h2>
        {latest && <span className="tag ok">{latest.activity_pct}% now</span>}
      </div>

      {samples.length ? (
        <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
          <polyline className="chart-line" points={points} fill="none" />
        </svg>
      ) : (
        <p className="empty">Waiting for fish-poll samples…</p>
      )}
      <p className="hint">% of the tank in motion, last {samples.length} poll ticks.</p>
    </section>
  )
}
