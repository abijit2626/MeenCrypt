import { useRef, useState } from 'react'
import { validateObservations } from '../api'
import Icon from './Icon'

function statRows(meta) {
  if (meta.frame_count != null) {
    return [
      ['frames', meta.frame_count],
      ['mean activity', `${meta.mean_activity_pct} %`],
      ['max fish', meta.max_fish_count],
      ['mean speed', meta.mean_speed],
      ['direction changes', meta.direction_changes],
    ]
  }
  return [
    ['samples', meta.sample_count],
    ['mean magnitude', meta.mean_magnitude],
    ['variance', meta.variance_magnitude],
    ['direction changes', meta.direction_changes],
  ]
}

export default function FishInput({ onValidated }) {
  const [text, setText] = useState('')
  const [error, setError] = useState('')
  const [valid, setValid] = useState(false)
  const [metadata, setMetadata] = useState(null)
  const fileRef = useRef(null)

  async function check() {
    setError('')
    setValid(false)
    setMetadata(null)
    let parsed
    try {
      parsed = JSON.parse(text)
    } catch {
      setError('invalid JSON')
      return
    }
    try {
      const meta = await validateObservations(parsed)
      setValid(true)
      setMetadata(meta)
      onValidated(parsed)
    } catch (err) {
      setValid(false)
      setMetadata(null)
      setError(String(err.message || err))
      onValidated(null)
    }
  }

  function onFile(e) {
    const file = e.target.files?.[0]
    if (!file) return
    const reader = new FileReader()
    reader.onload = () => setText(String(reader.result))
    reader.readAsText(file)
    e.target.value = ''
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2><Icon name="fish" /> 1 · Fish observations</h2>
        <div className="panel-actions">
          <button onClick={() => fileRef.current?.click()} className="btn ghost">Upload JSON</button>
          <input ref={fileRef} type="file" accept="application/json" hidden onChange={onFile} />
        </div>
      </div>
      <textarea
        className="mono"
        rows={10}
        spellCheck={false}
        placeholder='Paste the vision window JSON from your teammate: {"schema_version":2,"source":"fish_vision","frames":[...]}'
        value={text}
        onChange={(e) => { setText(e.target.value); setValid(false) }}
      />
      <div className="row-between">
        <button onClick={check} className="btn">Validate</button>
        {valid && <span className="tag ok">✓ SCHEMA OK</span>}
        {error && <span className="tag bad">{error.slice(0, 120)}</span>}
      </div>
      {metadata && (
        <div className="stats">
          {statRows(metadata).map(([label, value]) => (
            <div className="stat" key={label}>
              <span>{label}</span>
              <strong className="mono">{value}</strong>
            </div>
          ))}
        </div>
      )}
      <p className="hint">validation posts the window to the broker — it becomes the one Encryption binds.</p>
    </section>
  )
}