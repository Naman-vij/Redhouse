// FloorCard.jsx - the headline for an indoor floor plan: how much of the floor is watched,
// where it is not, and whether the protected spots and entrances are covered.
//
// "Watched" means the cameras together have at least a 1 in 4 chance of seeing someone standing
// there (backend engine/indoor.py). Every figure here comes from the backend; this file only
// rounds them and lays them out.

import { doorName, pct } from '../lib/format'

function Status({ items, empty }) {
  if (!items.length) return <p className="muted small">{empty}</p>
  return (
    <ul className="floor-status">
      {items.map((it) => (
        <li key={it.id} className={it.watched ? 'is-watched' : 'is-blind'}>
          <span className="floor-status-name">{doorName(it.id)}</span>
          <span className="floor-status-value">
            {it.watched ? 'Watched' : 'Not watched'}
            <span className="muted"> · {pct(it.chance_seen)}% chance of being seen</span>
          </span>
        </li>
      ))}
    </ul>
  )
}

export default function FloorCard({ floor, summary, changed }) {
  if (!floor) return null
  const now = pct(floor.watched_frac)
  const was = pct(floor.watched_original)

  return (
    <section className="floor-card" aria-labelledby="floor-heading">
      <h2 id="floor-heading" className="sr-only">Floor watched</h2>
      <p className="floor-number" aria-label={`${now} percent of the open floor is watched`}>
        {now}
        <span className="floor-pct">%</span>
      </p>
      <p className="floor-caption">
        of the open floor is watched.
        {changed && was !== now ? <span className="muted"> It was {was}% with the cameras as installed.</span> : null}
      </p>

      <div className="floor-meter" role="img" aria-label={`${now} percent watched, ${100 - now} percent blind`}>
        <span className="floor-meter-fill" style={{ width: `${now}%` }} />
      </div>

      <p className="floor-blind">
        {floor.blind_m2 > 0 ? (
          <>
            <strong>{floor.blind_m2} m²</strong> of {floor.open_floor_m2} m² has no camera on it.
          </>
        ) : (
          'No part of the open floor is blind.'
        )}
      </p>

      {floor.blind_spots.length > 0 && (
        <ul className="floor-spots" aria-label="Largest blind spots">
          {floor.blind_spots.slice(0, 4).map((b) => (
            <li key={b.id}>
              <strong>{b.area_m2} m²</strong>
              {b.near ? ` near the ${b.near.replace(/_/g, ' ')}` : ` around ${b.point[0]} m, ${b.point[1]} m`}
            </li>
          ))}
        </ul>
      )}

      {summary && !summary.startsWith('All ') && <p className="state-note">{summary}</p>}

      <h3 className="floor-sub">Protected spots</h3>
      <Status items={floor.assets} empty="No protected spots marked." />
      <h3 className="floor-sub">Entrances</h3>
      <Status items={floor.entries} empty="No entrances marked." />
    </section>
  )
}
