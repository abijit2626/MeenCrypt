import FishSource from '../components/FishSource'
import FishInput from '../components/FishInput'
import Pipeline from '../components/Pipeline'
import KeyPanel from '../components/KeyPanel'
import TamperTest from '../components/TamperTest'
import CryptoExplainer from '../components/CryptoExplainer'

// Fish telemetry + a live look at the crypto pipeline.
// (The actual diary encrypt/decrypt lives in the diary app.)
export default function DashboardView({ crypto }) {
  return (
    <div className="grid">
      <div className="col left">
        <FishSource />
        <details className="panel override-panel">
          <summary className="panel-head">🔧 Manual fish override <span className="hint">(optional)</span></summary>
          <FishInput onValidated={crypto.setOverrideFish} />
        </details>
        <CryptoExplainer />
      </div>

      <div className="col center">
        <Pipeline
          events={crypto.events}
          mode={crypto.mode}
          onRun={crypto.runEncrypt}
          busy={crypto.busy}
        />
        {crypto.result === 'encrypted' && (
          <div className="tag ok big" style={{ marginTop: 8 }}>✓ ENCRYPTED — fish: {crypto.source || 'collector'}</div>
        )}
        {crypto.result === 'error' && (
          <div className="tag bad big" style={{ marginTop: 8 }}>{crypto.error.slice(0, 140)}</div>
        )}
      </div>

      <div className="col right">
        <KeyPanel derived={crypto.result === 'encrypted'} />
        <TamperTest disabled={!crypto.pkg && !window.__fishPkg} />
      </div>
    </div>
  )
}