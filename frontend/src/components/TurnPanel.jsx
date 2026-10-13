// TurnPanel.jsx - turn one camera and watch what it does to the doors.
//
// Opens when you click a camera on the map. You can turn it three ways:
//   - drag the round handle on the map
//   - move the slider here
//   - press the left / right arrow keys while the camera is selected
//
// The wedge on the map follows instantly. The "was ... now ..." numbers come
// from the simulator a moment after you stop moving (see useDebounced.js).
//
// SIGN: positive = counter-clockwise, as seen from above on the plan. This is
// the same rule the backend and the fix recommendations use.

import { compass, doorName, pct, turnText } from '../lib/format'

export default function TurnPanel({ camera, angle, suggestion, doors, floor, pending, onTurn, onClose }) {
  const turned = angle !== 0

  return (
    <section className="turn-panel" aria-labelledby="turn-heading">
      <div className="turn-head">
        <h2 id="turn-heading" className="section-heading">Turn camera {camera.name}</h2>
        <button className="button button-quiet" onClick={onClose}>Done</button>
      </div>

      <div className="turn-controls">
        <div className="turn-slider">
          <label htmlFor="turn-range" className="field-label">
            {turnText(angle)}
            <span className="muted"> · now facing {compass(camera.heading_deg)}</span>
          </label>
          <input
            id="turn-range"
            type="range"
            min="-180"
            max="180"
            step="5"
            value={angle}
            onChange={(e) => onTurn(Number(e.target.value))}
            aria-valuetext={turnText(angle)}
          />
          <div className="turn-scale" aria-hidden="true">
            <span>180° clockwise</span>
            <span>counter-clockwise 180°</span>
          </div>
        </div>

        <div className="turn-buttons">
          <button className="button button-quiet" onClick={() => onTurn(Math.max(-180, angle - 15))}>15° clockwise</button>
          <button className="button button-quiet" onClick={() => onTurn(Math.min(180, angle + 15))}>15° counter-clockwise</button>
          {suggestion !== null && suggestion !== angle && (
            <button className="button" onClick={() => onTurn(suggestion)}>Use suggested: {turnText(suggestion)}</button>
          )}
          {turned && <button className="button button-quiet" onClick={() => onTurn(0)}>Put back</button>}
        </div>
      </div>

      <dl className="turn-impact" aria-busy={pending} aria-live="polite">
        {floor && (
          <div className={pending ? 'is-pending' : undefined}>
            <dt>Floor watched</dt>
            <dd>
              <span className="was">{pct(floor.watched_original)}%</span>
              <span aria-hidden="true"> → </span>
              <span className="sr-only"> now </span>
              <strong>{pct(floor.watched_frac)}%</strong>
              {!pending && pct(floor.watched_frac) > pct(floor.watched_original) && <span className="turn-better"> better</span>}
              {!pending && pct(floor.watched_frac) < pct(floor.watched_original) && <span className="turn-worse"> worse</span>}
            </dd>
          </div>
        )}
        {doors.map((d) => (
          <div key={d.door} className={pending ? 'is-pending' : undefined}>
            <dt>{doorName(d.door)}</dt>
            <dd>
              <span className="was">{pct(d.rate_original)}</span>
              <span aria-hidden="true"> → </span>
              <span className="sr-only"> now </span>
              <strong>{pct(d.rate)}</strong>
              <span className="turn-unit"> of 100 {floor ? 'reach it' : 'get in'} unseen</span>
              {!pending && pct(d.rate) < pct(d.rate_original) && <span className="turn-better"> better</span>}
              {!pending && pct(d.rate) > pct(d.rate_original) && <span className="turn-worse"> worse</span>}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
