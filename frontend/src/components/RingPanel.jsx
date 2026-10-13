// RingPanel.jsx - connect real Ring cameras.
//
// Three stages, matching useRing.js:
//   idle        paste a token
//   connected   pick which map camera each Ring device is
//   watching    live status, re-checked every 30 seconds
//
// WHY THE LINK STEP: Ring tells us a device's name and whether it is online.
// It does NOT tell us where the camera is mounted or which way it points.
// So you say "this Ring device is camera C1 on the plan", and only that
// camera takes its online/offline state from Ring.

import { useState } from 'react'
import { clock, clockSeconds } from '../lib/format'

export default function RingPanel({ ring, cameraNames, onClose }) {
  const [token, setToken] = useState('')
  const [picks, setPicks] = useState({}) // deviceId -> camera name chosen in the dropdown

  const linkedTo = Object.fromEntries(ring.devices.filter((d) => d.linked_to).map((d) => [d.linked_to, d]))
  const free = cameraNames.filter((n) => !linkedTo[n])

  function submitToken(e) {
    e.preventDefault()
    if (!token.trim()) return
    ring.connect(token).then(() => setToken('')) // forget the token text in the form once sent
  }

  return (
    <section className="ring-panel" aria-labelledby="ring-heading">
      <div className="ring-panel-head">
        <h2 id="ring-heading" className="section-heading">Connect Ring</h2>
        <button className="button button-quiet" onClick={onClose}>Close</button>
      </div>

      {ring.error && <p className="problem" role="alert">{ring.error}</p>}

      {ring.phase === 'idle' && (
        <form onSubmit={submitToken} className="ring-form">
          <p>
            Paste a token from the Ring Developer Playground. It stays in this browser tab, is never saved, and
            stops working after 30 minutes.
          </p>
          <label htmlFor="ring-token" className="field-label">Ring token</label>
          <input
            id="ring-token"
            type="password"
            autoComplete="off"
            spellCheck={false}
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Paste token here"
          />
          <button className="button button-primary" type="submit" disabled={ring.busy || !token.trim()}>
            {ring.busy ? 'Reading…' : 'Read my Ring devices'}
          </button>
        </form>
      )}

      {ring.phase !== 'idle' && (
        <>
          <p>
            Found {ring.devices.length} Ring {ring.devices.length === 1 ? 'device' : 'devices'}. Choose which
            camera on the plan each one is.
          </p>
          <ul className="ring-devices">
            {ring.devices.map((d) => (
              <li key={d.device_id} className="ring-device">
                <div>
                  <span className="ring-device-name">{d.name}</span>
                  <span className={d.online ? 'status status-on' : 'status status-off'}>
                    {d.online ? 'Online' : 'Offline'}
                  </span>
                  {d.reported_at && <span className="muted small"> reported {clock(d.reported_at)}</span>}
                </div>
                {d.linked_to ? (
                  <div className="ring-linked">
                    <span>Linked to camera {d.linked_to}</span>
                    <button className="button button-quiet" onClick={() => ring.unlink(d.linked_to)}>Unlink</button>
                  </div>
                ) : (
                  <div className="ring-link-row">
                    <label className="sr-only" htmlFor={`pick-${d.device_id}`}>Camera for {d.name}</label>
                    <select
                      id={`pick-${d.device_id}`}
                      value={picks[d.device_id] ?? free[0] ?? ''}
                      onChange={(e) => setPicks({ ...picks, [d.device_id]: e.target.value })}
                      disabled={free.length === 0}
                    >
                      {free.map((n) => <option key={n} value={n}>Camera {n}</option>)}
                    </select>
                    <button
                      className="button button-primary"
                      disabled={ring.busy || free.length === 0}
                      onClick={() => ring.link(picks[d.device_id] ?? free[0], d.device_id)}
                    >
                      Link and watch
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>

          {ring.phase === 'watching' && (
            <p className="muted">
              Checking Ring every {ring.pollSeconds} seconds. Last check {clockSeconds(ring.lastChecked)}.
            </p>
          )}
          <div className="ring-actions">
            <button className="button button-quiet" onClick={ring.refresh} disabled={ring.busy}>
              {ring.busy ? 'Checking…' : 'Check now'}
            </button>
            <button className="button button-quiet" onClick={ring.disconnect}>Disconnect and forget token</button>
          </div>
        </>
      )}
    </section>
  )
}
