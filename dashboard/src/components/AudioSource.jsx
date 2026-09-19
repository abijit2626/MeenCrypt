import { useEffect, useState } from 'react'
import { currentAudio, subscribeAudio } from '../api'
import Icon from './Icon'

function fmt(value, digits = 2) {
  if (value == null) return '—'
  if (typeof value === 'number') return Number(value).toFixed(digits)
  return String(value)
}

// Mirrors FishSource.jsx, but for the ESP32 mic's Sound_Level windows -
// see server/main.py's audio poll loop for where these events come from.
export default function AudioSource() {
  const [connected, setConnected] = useState(false)
  const [update, setUpdate] = useState(null)   // {source, received_at, metadata}
  const [error, setError] = useState('')

  useEffect(() => {
    let es = subscribeAudio((data) => {
      setConnected(true)
      setUpdate(data)
      setError('')
    })
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    // Seed with any cached window.
    currentAudio()
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
        <h2><Icon name="mic" /> 3 · Live ESP32 mic feed</h2>
      </div>

      <div className="feed-status">
        <span className={`dot ${connected ? 'on' : 'off'}`} />
        <span>{connected ? 'connected · auto-ingesting the ESP32 mic' : 'disconnected · reconnecting…'}</span>
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
          <div className="stat">
            <span>readings</span>
            <strong className="mono">{meta.reading_count}</strong>
          </div>
          <div className="stat">
            <span>window</span>
            <strong className="mono">{fmt(meta.window_duration_s, 1)} s</strong>
          </div>
          <div className="stat">
            <span>mean level</span>
            <strong className="mono">{fmt(meta.mean_level)}</strong>
          </div>
          <div className="stat">
            <span>min / max</span>
            <strong className="mono">{meta.min_level ?? '—'} / {meta.max_level ?? '—'}</strong>
          </div>
        </div>
      }
      {update?.cached && <p className="hint">cached window · a live mic capture updates this automatically</p>}
      {!update && !error && <p className="hint">Waiting for a mic window… (connect the ESP32 and set FISHRAND_AUDIO_PORT)</p>}
      <p className="hint">
        Used automatically at encrypt time only when fish quality needs it (MEDIUM/BAD).
      </p>
    </section>
  )
}
