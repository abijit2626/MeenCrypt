import { useEffect, useState } from 'react'
import { currentObservations, subscribeFish } from '../api'

function fmt(value, digits = 2) {
  if (value == null) return '—'
  if (typeof value === 'number') return Number(value).toFixed(digits)
  return String(value)
}

function statRows(meta) {
  if (meta.frame_count != null) {
    return [
      ['frames', fmt(meta.frame_count, 0)],
      ['mean activity', `${fmt(meta.mean_activity_pct)} %`],
      ['max fish', meta.max_fish_count],
      ['mean speed', fmt(meta.mean_speed)],
      ['speed variance', fmt(meta.variance_magnitude)],
      ['direction changes', meta.direction_changes],
      ['path length', fmt(meta.total_path_length)],
      ['window', `${(meta.time_span_ns / 1e6).toFixed(0)} s`],
    ]
  }
  return [
    ['samples', meta.sample_count],
    ['time span', `${(meta.time_span_ns / 1e6).toFixed(1)} ms`],
    ['mean magnitude', fmt(meta.mean_magnitude)],
    ['variance', fmt(meta.variance_magnitude)],
    ['direction changes', meta.direction_changes],
    ['path length', fmt(meta.total_path_length)],
  ]
}

export default function FishSource() {
  const [connected, setConnected] = useState(false)
  const [update, setUpdate] = useState(null)   // {source, received_at, metadata}
  const [error, setError] = useState('')

  useEffect(() => {
    let es = subscribeFish((data) => {
      setConnected(true)
      setUpdate(data)
      setError('')
    })
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    // Seed with any cached window.
    currentObservations()
      .then((data) => data && setUpdate({
        source: data.source,
        received_at: data.received_at,
        cached: true,
        metadata: data.metadata,
      }))
      .catch(() => {})
    return () => es.close()
  }, [])

  const meta = update?.metadata
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>🐟 1 · Live fish vision feed</h2>
      </div>

      <div className="feed-status">
        <span className={`dot ${connected ? 'on' : 'off'}`} />
        <span>{connected ? 'connected · auto-ingesting the vision engine' : 'disconnected · reconnecting…'}</span>
      </div>

      {error && <div className="tag bad">{error.slice(0, 120)}</div>}

      {update && (
        <>
          <div className="feed-row mono">
            <span>source</span>
            <strong>{update.source}</strong>
          </div>
          {update.received_at && (
            <div className="feed-row mono">
              <span>received</span>
              <strong>{new Date(update.received_at).toLocaleTimeString()}</strong>
            </div>
          )}
        </>
      )}

      {meta &&
        <div className="stats">
          {statRows(meta).map(([label, value]) => (
            <div className="stat" key={label}>
              <span>{label}</span>
              <strong className="mono">{value}</strong>
            </div>
          ))}
        </div>
      }
      {update?.cached && <p className="hint">cached window · a live vision frame updates this automatically</p>}
      {!update && !error && <p className="hint">Waiting for a vision window…</p>}
      <p className="hint">
        Encryption collects the current window automatically — no pasting.
      </p>
    </section>
  )
}