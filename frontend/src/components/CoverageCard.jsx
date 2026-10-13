// CoverageCard.jsx - "Intruder coverage": for the door you are looking at, how many of 100 simulated intruders
// were seen and how many got in. Two gauges and 100 dots say the same thing three ways.
//
// Every number is the simulator's figure, rounded once (pct in format.js). Nothing is calculated here except
// 100 minus a rounded figure, so the two gauges always add to 100.

import { INTRUDERS, doorName, humanise, pct } from '../lib/format'

const R = 42
const C = 2 * Math.PI * R

function Gauge({ value, unit, label, tone }) {
  const share = Math.max(0, Math.min(100, value)) / 100
  return (
    <div className="gauge">
      <div className="gauge-ring" role="img" aria-label={`${value} ${unit}. ${label}`}>
        <svg viewBox="0 0 100 100" width="104" height="104" aria-hidden="true">
          <circle cx="50" cy="50" r={R} className="gauge-track" />
          <circle
            cx="50" cy="50" r={R}
            className={`gauge-fill gauge-${tone}`}
            strokeDasharray={`${share * C} ${C}`}
            transform="rotate(-90 50 50)"
          />
        </svg>
        <span className="gauge-number">
          <strong>{value}</strong>
          <span>{unit}</span>
        </span>
      </div>
      <span className="gauge-label">{label}</span>
    </div>
  )
}

export default function CoverageCard({ doors, active, onSelect, rate, indoor, floor, archetype, summary, pending }) {
  const unseen = pct(rate)
  const seen = 100 - unseen
  const intruder = INTRUDERS.find((i) => i.id === archetype)

  return (
    <section className="card coverage-card" id="simulation" aria-labelledby="coverage-title" aria-busy={pending}>
      <h2 id="coverage-title" className="card-title">Intruder coverage</h2>
      <p className="card-sub">
        {intruder ? `${intruder.name}: ${intruder.blurb.charAt(0).toLowerCase()}${intruder.blurb.slice(1)} ` : ''}
        100 simulated intruders, {indoor ? 'to the spot' : 'to the door'} you pick.
      </p>

      <div className="door-chips" role="tablist" aria-label={indoor ? 'Protected spot to inspect' : 'Door to inspect'}>
        {doors.map((d) => (
          <button
            key={d.door}
            role="tab"
            aria-selected={d.door === active}
            className={d.door === active ? 'chip is-active' : 'chip'}
            onClick={() => onSelect(d.door)}
          >
            {doorName(d.door)}
            <span className="chip-number">{pct(d.rate)}</span>
          </button>
        ))}
      </div>

      <div className={pending ? 'gauges is-pending' : 'gauges'}>
        {indoor && floor ? (
          <Gauge value={pct(floor.watched_frac)} unit="%" label="Floor watched" tone="good" />
        ) : (
          <Gauge value={seen} unit="/ 100" label="Seen by cameras" tone="good" />
        )}
        <Gauge
          value={unseen}
          unit="/ 100"
          label={indoor ? 'Reach it unseen' : 'Not seen by cameras'}
          tone="bad"
        />
      </div>

      <div className="dots" role="img" aria-label={`${unseen} of 100 intruders ${indoor ? 'reach it' : 'get in'} unseen, ${seen} are seen`}>
        {Array.from({ length: 100 }, (_, i) => (
          <span key={i} className={i < seen ? 'dot dot-seen' : 'dot dot-in'} />
        ))}
      </div>
      <p className="dots-key">
        <span><i className="dot dot-seen" /> Seen by a camera</span>
        <span><i className="dot dot-in" /> {indoor ? 'Reaches it unseen' : 'Gets in unseen'}</span>
      </p>

      {summary && !summary.startsWith('All ') && <p className="state-note">{humanise(summary)}</p>}
    </section>
  )
}
