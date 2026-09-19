import Icon from './Icon'

// Panels 6/7: HKDF and AES+RSA-wrap latency (p50/p95), same tile shape for
// both so there's one component instead of two near-duplicates - see
// DashboardView.jsx, which instantiates this twice against
// metrics.latency_ms.kdf / .wrap. `icon` is an Icon.jsx name, not an emoji.
export default function LatencyStat({ icon, title, hint, latency }) {
  const { p50, p95, n } = latency
  return (
    <section className="panel">
      <div className="panel-head">
        <h2><Icon name={icon} /> {title}</h2>
      </div>
      {n > 0 ? (
        <div className="stats">
          <div className="stat">
            <span>p50</span>
            <strong className="mono">{p50} ms</strong>
          </div>
          <div className="stat">
            <span>p95</span>
            <strong className="mono">{p95} ms</strong>
          </div>
        </div>
      ) : (
        <p className="empty">No {title.toLowerCase()} samples yet - run an encrypt.</p>
      )}
      <p className="hint">{hint} · {n} sample{n === 1 ? '' : 's'}</p>
    </section>
  )
}
