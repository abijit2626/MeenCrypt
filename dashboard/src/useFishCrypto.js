import { useState } from 'react'
import { streamPipeline } from './api'

// Drives the crypto pipeline on the dashboard: encrypts the current fish
// window with a demo plaintext so the event stream streams in live. Real
// diary encrypt/decrypt lives in the diary app.
export default function useFishCrypto() {
  const [overrideFish, setOverrideFish] = useState(null)
  const [events, setEvents] = useState([])
  const [mode, setMode] = useState('encrypt')
  const [busy, setBusy] = useState(false)
  const [pkg, setPkg] = useState(null)
  const [source, setSource] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  function reset(next) {
    setEvents([])
    setResult(next)
    setError('')
  }

  async function runEncrypt() {
    setBusy(true)
    setMode('encrypt')
    reset('encrypting')
    try {
      const body = { plaintext: demoPlaintext() }
      if (overrideFish) body.fish_json = overrideFish   // manual override only
      const payload = await streamPipeline('/api/encrypt', body, (evt) => setEvents((prev) => [...prev, evt]))
      window.__fishPkg = payload.package
      setPkg(payload.package)
      setSource(payload.package?.metadata?.fish_source || null)
      setResult('encrypted')
    } catch (err) {
      setError(String(err.message || err))
      setResult('error')
    } finally {
      setBusy(false)
    }
  }

  return {
    overrideFish, setOverrideFish,
    events, mode, busy, pkg, source, result, error,
    runEncrypt,
  }
}

function demoPlaintext() {
  return `FISHRAND pipeline demo · ${new Date().toISOString()}\n\nThis message is bound to the current fish vision window.`
}