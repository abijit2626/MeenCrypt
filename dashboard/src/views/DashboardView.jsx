import FishSource from '../components/FishSource'
import AudioSource from '../components/AudioSource'
import FishInput from '../components/FishInput'
import Pipeline from '../components/Pipeline'
import KeyPanel from '../components/KeyPanel'
import TamperTest from '../components/TamperTest'
import CryptoExplainer from '../components/CryptoExplainer'
import FishActivityChart from '../components/FishActivityChart'
import FishCountStat from '../components/FishCountStat'
import VaultStateStat from '../components/VaultStateStat'
import QualityTierBars from '../components/QualityTierBars'
import OpsRateChart from '../components/OpsRateChart'
import LatencyStat from '../components/LatencyStat'
import VisionPreview from '../components/VisionPreview'
import Icon from '../components/Icon'
import { useMetrics } from '../lib/useMetrics'

// Fish telemetry + a live look at the crypto pipeline.
// (The actual diary encrypt/decrypt lives in the diary app.)
export default function DashboardView({ crypto }) {
  const metrics = useMetrics()

  return (
    <>
      <div className="grid">
        <div className="col left">
          <FishSource />
          <AudioSource />
          <details className="panel override-panel">
            <summary className="panel-head"><Icon name="wrench" /> Manual fish override <span className="hint">(optional)</span></summary>
            <FishInput onValidated={crypto.setOverrideFish} />
          </details>
        </div>

        <div className="col center">
          <Pipeline
            events={crypto.events}
            mode={crypto.mode}
            onRun={crypto.runEncrypt}
            busy={crypto.busy}
          />
          {crypto.result === 'encrypted' && (
            <div className="tag ok big">✓ ENCRYPTED — fish: {crypto.source || 'collector'}</div>
          )}
          {crypto.result === 'error' && (
            <div className="tag bad big">{crypto.error.slice(0, 140)}</div>
          )}
        </div>

        <div className="col right">
          <KeyPanel derived={crypto.result === 'encrypted'} />
          <TamperTest disabled={!crypto.pkg && !window.__fishPkg} />
        </div>
      </div>

      <h2 className="section-title"><Icon name="barChart" /> Telemetry</h2>
      <div className="telemetry-grid">
        <FishActivityChart metrics={metrics} />
        <FishCountStat metrics={metrics} />
        <VaultStateStat />
        <QualityTierBars metrics={metrics} />
        <OpsRateChart metrics={metrics} />
        <LatencyStat
          icon="key" title="HKDF latency" hint="key derivation, per encrypt"
          latency={metrics.latency_ms.kdf}
        />
        <LatencyStat
          icon="shield" title="AES+RSA wrap latency" hint="AES-GCM encrypt + RSA-OAEP wrap, per encrypt"
          latency={metrics.latency_ms.wrap}
        />
        <VisionPreview />
      </div>

      <CryptoExplainer />
    </>
  )
}