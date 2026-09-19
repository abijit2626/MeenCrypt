import Icon from './components/Icon'
import useFishCrypto from './useFishCrypto'
import DashboardView from './views/DashboardView'
import './App.css'

// A loose school of fish drifting behind everything - plain SVG shapes
// (Icon.jsx's `fish`), sized/spaced/timed by the fN classes in App.css.
// aria-hidden: purely decorative, never carries information.
function AquariumBackground() {
  const lanes = [1, 2, 3, 4, 5, 6, 7, 8]
  return (
    <div className="aquarium-bg" aria-hidden="true">
      {lanes.map((n) => (
        <span className={`swim-fish f${n} ${n % 2 === 0 ? 'reverse' : ''}`} key={n}>
          <Icon name="fish" size={20 + (n % 4) * 8} />
        </span>
      ))}
    </div>
  )
}

export default function App() {
  const crypto = useFishCrypto()

  return (
    <div className="layout">
      <AquariumBackground />
      <header className="topbar">
        <div>
          <h1>MEENCRYPT</h1>
          <p className="sub">physical entropy infrastructure · fish telemetry + crypto pipeline</p>
        </div>
        <nav className="nav">
          <span className="nav-btn active"><Icon name="fish" /> Fish activity</span>
          <span className="tag">diary app: <code>/diary</code></span>
        </nav>
      </header>

      <DashboardView crypto={crypto} />

      <footer className="foot">
        <span className="tag">the fish contributes → your RSA private key holds the secret</span>
        <span className="hint">fish data is conditioned (SHA-256), not trusted entropy. AES key stays HIDDEN.</span>
      </footer>
    </div>
  )
}