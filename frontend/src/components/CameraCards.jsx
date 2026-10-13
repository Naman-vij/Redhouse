// CameraCards.jsx - "Camera setup": one card per camera, and a card for the suggested new camera.
//
// A card lets you turn a camera (slider or the two rotate buttons), find it on the map, and, for a simulated
// camera, switch it off to see what would happen. A camera linked to a Ring device has no switch: Ring says
// whether it is online, and letting you override that would blur real and pretend.
//
// SIGN: positive = counter-clockwise, as seen from above on the plan (the same rule as everywhere else).
// The little dial shows where the camera points now. It is drawn from the heading the simulator uses.

import { clock, compass, doorName, pct, turnText } from '../lib/format'
import Icon from './Icon'

const rad = (d) => (d * Math.PI) / 180

/** A small compass dial: north at the top, the camera's view as a wedge. */
function Dial({ heading, fov, online }) {
  const r = 30
  const a0 = rad(heading - fov / 2)
  const a1 = rad(heading + fov / 2)
  const p = (a) => [50 + r * Math.cos(a), 50 - r * Math.sin(a)]
  const [x0, y0] = p(a0)
  const [x1, y1] = p(a1)
  const [cx, cy] = p(rad(heading))
  return (
    <svg className={online ? 'dial' : 'dial is-off'} viewBox="0 0 100 100" aria-hidden="true">
      <circle cx="50" cy="50" r="44" className="dial-face" />
      <path d={`M50 50 L${x0} ${y0} A${r} ${r} 0 ${fov > 180 ? 1 : 0} 0 ${x1} ${y1} Z`} className="dial-wedge" />
      <line x1="50" y1="50" x2={cx} y2={cy} className="dial-line" />
      <circle cx="50" cy="50" r="4.5" className="dial-dot" />
      <text x="50" y="13" textAnchor="middle" className="dial-n">N</text>
    </svg>
  )
}

function Card({ cam, live, turn, heading, selected, impact, pending, onSelect, onTurn, onToggle }) {
  const ring = live[cam.name]
  const online = cam.online
  return (
    <li className={['cam-card', selected ? 'is-selected' : '', online ? '' : 'is-off'].join(' ')}>
      <div className="cam-head">
        <h3>{cam.name}</h3>
        <span className={online ? 'pill pill-on' : 'pill pill-off'}>{online ? 'Online' : 'Offline'}</span>
        {ring && <span className="pill pill-ring">Ring</span>}
      </div>

      <div className="cam-body">
        <Dial heading={heading} fov={cam.fov_deg} online={online} />
        <div className="cam-controls">
          <label htmlFor={`turn-${cam.name}`} className="cam-angle">
            <span>Angle</span>
            <strong>{turnText(turn)}</strong>
          </label>
          <input
            id={`turn-${cam.name}`}
            type="range"
            min="-180"
            max="180"
            step="5"
            value={turn}
            onChange={(e) => onTurn(Number(e.target.value))}
            aria-valuetext={`${turnText(turn)}, now facing ${compass(heading)}`}
          />
          <p className="cam-facing">Facing {compass(heading)}</p>
        </div>
      </div>

      <div className="cam-buttons">
        <button className="button button-small" onClick={() => onTurn(Math.min(180, turn + 15))}>
          <Icon name="left" size={15} /> Rotate left
        </button>
        <button className="button button-small" onClick={() => onTurn(Math.max(-180, turn - 15))}>
          Rotate right <Icon name="right" size={15} />
        </button>
        <button className="button button-small button-pin" aria-pressed={selected} onClick={onSelect}>
          <Icon name="pin" size={15} /> {selected ? 'On the map' : 'Show on map'}
        </button>
        {ring ? (
          <p className="cam-source">
            Live from Ring: {ring.deviceName}{ring.reportedAt ? `, ${clock(ring.reportedAt)}` : ''}
          </p>
        ) : (
          <button className="button button-small" aria-pressed={!online} onClick={onToggle}>
            <Icon name="power" size={15} /> {online ? 'Simulate offline' : 'Bring back'}
          </button>
        )}
      </div>

      {selected && (
        <dl className="cam-impact" aria-busy={pending} aria-live="polite">
          {impact.floor && (
            <div className={pending ? 'is-pending' : undefined}>
              <dt>Floor watched</dt>
              <dd>
                <span className="was">{pct(impact.floor.watched_original)}%</span> → <strong>{pct(impact.floor.watched_frac)}%</strong>
              </dd>
            </div>
          )}
          {impact.doors.map((d) => (
            <div key={d.door} className={pending ? 'is-pending' : undefined}>
              <dt>{doorName(d.door)}</dt>
              <dd>
                <span className="was">{pct(d.rate_original)}</span> → <strong>{pct(d.rate)}</strong>
                <span className="unit"> of 100 {impact.floor ? 'reach it' : 'get in'}</span>
                {!pending && pct(d.rate) < pct(d.rate_original) && <em className="better"> better</em>}
                {!pending && pct(d.rate) > pct(d.rate_original) && <em className="worse"> worse</em>}
              </dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}

export default function CameraCards({
  cameras, live, turns, headings, selected, doors, floor, pending, plan, preview,
  onSelect, onTurn, onToggle, onPreview,
}) {
  const adds = plan.status === 'ok' ? plan.data.more_cameras.additions : []
  const canSuggest = plan.status === 'ok' && plan.data.more_cameras.needed && adds.length > 0

  return (
    <section id="cameras" aria-labelledby="cameras-title">
      <h2 id="cameras-title" className="sr-only">Camera setup</h2>
      <ul className="cam-grid">
        {cameras.map((cam) => (
          <Card
            key={cam.name}
            cam={cam}
            live={live}
            turn={turns[cam.name] ?? 0}
            heading={headings[cam.name] ?? cam.heading_deg}
            selected={selected === cam.name}
            pending={pending}
            impact={{ doors, floor }}
            onSelect={() => onSelect(cam.name)}
            onTurn={(a) => onTurn(cam.name, a)}
            onToggle={() => onToggle(cam.name)}
          />
        ))}
        <li className="cam-card cam-add">
          <span className="cam-add-icon"><Icon name="plus" size={26} /></span>
          <h3>Suggested camera</h3>
          <p>
            {plan.status === 'loading'
              ? 'Looking for the best spot.'
              : canSuggest
                ? 'A new camera in the spot that helps most. This only previews it on the map.'
                : 'More cameras would not help much here.'}
          </p>
          <button
            className="button button-primary"
            disabled={!canSuggest}
            aria-pressed={preview === 'more'}
            onClick={() => onPreview('more')}
          >
            <Icon name="spark" size={16} /> {preview === 'more' ? 'Remove suggestion' : 'Suggest best position'}
          </button>
        </li>
      </ul>
    </section>
  )
}
