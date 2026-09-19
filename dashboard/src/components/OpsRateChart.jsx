import { linePoints } from '../lib/chart'
import Icon from './Icon'

const W = 260
const H = 64

// Panel 5/7: encrypt vs decrypt ops/min, from metrics.ops.{encrypt,decrypt}.history
// (one point per server broadcast tick - server/metrics.py's tick_rate_history).
export default function OpsRateChart({ metrics }) {
  const encryptHistory = metrics.ops.encrypt.history
  const decryptHistory = metrics.ops.decrypt.history
  const allRates = [...encryptHistory, ...decryptHistory].map((p) => p.rate)
  const max = Math.max(1, ...allRates)

  const encryptPoints = linePoints(encryptHistory.map((p) => p.rate), { width: W, height: H, min: 0, max })
  const decryptPoints = linePoints(decryptHistory.map((p) => p.rate), { width: W, height: H, min: 0, max })
  const hasHistory = encryptHistory.length > 1 || decryptHistory.length > 1

  return (
    <section className="panel">
      <div className="panel-head">
        <h2><Icon name="refresh" /> Encrypt / decrypt rate</h2>
      </div>
      <div className="row-between tight">
        <span className="tag ok">encrypt · {metrics.ops.encrypt.rate_per_min}/min</span>
        <span className="tag warn">decrypt · {metrics.ops.decrypt.rate_per_min}/min</span>
      </div>
      {hasHistory ? (
        <svg className="chart-svg" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
          <polyline className="chart-line" points={encryptPoints} fill="none" />
          <polyline className="chart-line alt" points={decryptPoints} fill="none" />
        </svg>
      ) : (
        <p className="empty">Run an encrypt or decrypt to start the rate history.</p>
      )}
    </section>
  )
}
