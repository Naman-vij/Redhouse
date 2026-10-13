// CameraStrip.jsx - one entry per camera under the map.
//
// Each camera is either:
//   LIVE       its online/offline state is read from a real Ring device
//   SIMULATED  its state is whatever you set here (a what-if)
//
// NOTE: a live camera has no switch, on purpose. Its state comes from
// Ring, and letting you override it would blur "real" and "pretend".

import { clock } from '../lib/format'

export default function CameraStrip({ cameras, live, onToggle }) {
  return (
    <section aria-labelledby="cameras-heading" className="camera-strip">
      <h2 id="cameras-heading" className="section-heading">Cameras</h2>
      <ul className="camera-list">
        {cameras.map((cam) => {
          const ring = live[cam.name]
          return (
            <li key={cam.name} className={cam.online ? 'camera-item' : 'camera-item is-off'}>
              <div className="camera-item-head">
                <span className="camera-item-name">{cam.name}</span>
                <span className={cam.online ? 'status status-on' : 'status status-off'}>
                  {cam.online ? 'Online' : 'Offline'}
                </span>
              </div>
              {ring ? (
                <p className="camera-item-source">
                  Live from Ring: {ring.deviceName}
                  {ring.reportedAt ? `, reported ${clock(ring.reportedAt)}` : ''}
                </p>
              ) : (
                <p className="camera-item-source">Simulated</p>
              )}
              {ring ? (
                <p className="muted small">Status comes from Ring.</p>
              ) : (
                <button className="button button-quiet" aria-pressed={!cam.online} onClick={() => onToggle(cam.name)}>
                  {cam.online ? `Take ${cam.name} offline` : `Bring ${cam.name} back`}
                </button>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
