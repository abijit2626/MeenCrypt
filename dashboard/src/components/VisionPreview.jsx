import { useEffect, useRef, useState } from 'react'
import { VISION_STREAM_URL } from '../api'
import Icon from './Icon'

// Bonus panel: a live look at vision.py's own camera preview, proxied as
// an MJPEG stream (server/main.py's /api/vision/stream just re-reads the
// one frame file vision.py writes - see server/config.py's
// FISHRAND_VISION_FRAME_PATH docs). No polling loop needed: the <img> tag
// holds one persistent connection on its own.
export default function VisionPreview() {
  const [connected, setConnected] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const retryTimer = useRef(null)

  useEffect(() => () => clearTimeout(retryTimer.current), [])

  function retry() {
    setConnected(false)
    setAttempt((a) => a + 1)
  }

  return (
    <section className="panel vision-panel">
      <div className="panel-head">
        <h2><Icon name="camera" /> Tank camera</h2>
      </div>
      <div className="feed-status">
        <span className={`dot ${connected ? 'on' : 'off'}`} />
        <span>{connected ? 'live' : 'no frame yet · is vision.py running with a display?'}</span>
      </div>

      <div className="vision-frame">
        <img
          key={attempt}
          src={`${VISION_STREAM_URL}?retry=${attempt}`}
          alt="Live tank camera preview"
          onLoad={() => setConnected(true)}
          onError={() => {
            setConnected(false)
            retryTimer.current = setTimeout(retry, 3000)
          }}
        />
        {!connected && <div className="vision-placeholder">waiting for a camera frame…</div>}
      </div>
    </section>
  )
}
