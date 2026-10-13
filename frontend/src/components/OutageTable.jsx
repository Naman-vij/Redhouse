// OutageTable.jsx - "if a camera fails, which door suffers?"
//
// One row per camera. Each cell is the share of 100 intruders who would
// get in at that door if ONLY that camera were off, and how many points
// worse that is than normal. The redder the cell, the bigger the jump.
//
// NOTE: the figures are always printed in the cell, not only coloured,
// so the table reads the same without colour.

import { doorName, pct } from '../lib/format'

export default function OutageTable({ data, offline, live, onShow }) {
  if (!data) return null
  const indoor = data.mode === 'indoor'
  const doors = data.cameras[0]?.effects.map((e) => e.door) ?? []

  return (
    <section className="outage" aria-labelledby="outage-heading">
      <h2 id="outage-heading" className="section-heading">If a camera fails</h2>
      <p className="muted outage-intro">
        {indoor
          ? 'Each row switches off one camera. The first column is how much of the floor is still watched. The others show how many of 100 intruders reach each protected spot unseen, and the change from normal.'
          : 'Each row switches off one camera. Cells show how many of 100 intruders get in at each door, and the change from normal.'}
      </p>

      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">Camera that fails</th>
              {indoor && <th scope="col" className="num">Floor still watched</th>}
              {doors.map((d) => (
                <th key={d} scope="col" className="num">{doorName(d)}</th>
              ))}
              <th scope="col"><span className="sr-only">Show on map</span></th>
            </tr>
          </thead>
          <tbody>
            {data.cameras.map((row) => {
              const isLive = live.includes(row.camera)
              const isOff = offline.includes(row.camera)
              return (
                <tr key={row.camera} className={isOff ? 'is-off' : undefined}>
                  <th scope="row">
                    {row.camera}
                    {isLive && <span className="tag">live</span>}
                  </th>
                  {indoor && row.floor && (
                    <td
                      className="num cell"
                      style={{ '--heat': `${Math.min(70, (pct(row.floor.watched_before) - pct(row.floor.watched_after)) * 1.6)}%` }}
                    >
                      <span className="cell-value">{pct(row.floor.watched_after)}%</span>
                      <span className="cell-change">
                        {pct(row.floor.watched_after) === pct(row.floor.watched_before)
                          ? 'no change'
                          : `${pct(row.floor.watched_after) - pct(row.floor.watched_before)}`}
                      </span>
                    </td>
                  )}
                  {row.effects.map((e) => {
                    const points = pct(e.after) - pct(e.before)
                    return (
                      <td
                        key={e.door}
                        className="num cell"
                        style={{ '--heat': `${Math.min(70, Math.abs(points) * 1.6)}%` }}
                      >
                        <span className="cell-value">{pct(e.after)}</span>
                        <span className="cell-change">{points > 0 ? `+${points}` : points === 0 ? 'no change' : points}</span>
                      </td>
                    )
                  })}
                  <td className="act">
                    {isLive ? (
                      <span className="muted small">Set by Ring</span>
                    ) : (
                      <button className="button button-quiet" aria-pressed={isOff} onClick={() => onShow(row.camera)}>
                        {isOff ? 'Remove from map' : 'Show on map'}
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {data.single_points_of_failure.length > 0 && (
        <p className="spof">
          {indoor
            ? 'Cameras whose failure blinds at least 10 points of the floor: '
            : 'Cameras whose failure leaves a door at least 10 points worse: '}
          {data.single_points_of_failure.map((s) => s.camera).join(', ')}.
        </p>
      )}
    </section>
  )
}
