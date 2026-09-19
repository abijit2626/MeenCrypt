import Icon from './Icon'

// Panel 2/7: latest fish count, from the same rolling window
// FishActivityChart draws (metrics.fish_activity). The count itself can
// legitimately repeat between ticks (a still tank), so the dot + timestamp
// below are what actually shows this is live, not just a static number.
export default function FishCountStat({ metrics }) {
  const samples = metrics.fish_activity
  const latest = samples[samples.length - 1]

  return (
    <section className="panel">
      <div className="panel-head">
        <h2><Icon name="fish" /> Fish count</h2>
      </div>
      <div className="feed-status">
        <span className={`dot ${metrics.connected ? 'on' : 'off'}`} />
        <span>{metrics.connected ? 'live' : 'disconnected · reconnecting…'}</span>
      </div>
      <div className="stat-big">
        <span className="stat-big-value">{latest ? latest.fish_count : '—'}</span>
        <span className="hint mono">
          {latest ? `as of ${new Date(latest.t).toLocaleTimeString()}` : 'no samples yet'}
        </span>
      </div>
    </section>
  )
}
