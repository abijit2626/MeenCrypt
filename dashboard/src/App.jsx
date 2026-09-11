import useFishCrypto from './useFishCrypto'
import DashboardView from './views/DashboardView'
import './App.css'

export default function App() {
  const crypto = useFishCrypto()

  return (
    <div className="layout">
      <header className="topbar">
        <div>
          <h1>FISHRAND</h1>
          <p className="sub">physical entropy infrastructure · fish telemetry + crypto pipeline</p>
        </div>
        <nav className="nav">
          <span className="nav-btn active">🐟 Fish activity</span>
          <span className="tag">diary app: <code>/diary</code></span>
        </nav>
      </header>

      <DashboardView crypto={crypto} />

      <footer className="foot">
        <span className="tag">the fish contributes → the USB code holds the secret</span>
        <span className="hint">fish data is conditioned (SHA-256), not trusted entropy. AES key stays HIDDEN.</span>
      </footer>
    </div>
  )
}