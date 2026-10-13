// BestSetup.jsx - "what is the best I can do, and do I need more cameras?"
//
// Two steps, because that is the order a person would act in:
//   1. turn the cameras you already have to their best angles (free)
//   2. only if a door is STILL exposed, add cameras (costs money)
//
// A strip of three numbers at the top shows the whole story at a glance:
// the most exposed door today, after turning, after adding.
//
// NOTE: every number and sentence here comes from the backend (routes/plan.py).
// The search used one set of intruders; the figures are from a second set it
// never saw, so a layout that only looked good by luck would show up here.

import { doorName, pct } from '../lib/format'

/** The most exposed door in a list of {door, rate}. */
function worst(rows) {
  return rows.reduce((a, b) => (b.rate > a.rate ? b : a), rows[0])
}

function Doors({ rows, unit = 'of 100' }) {
  return (
    <dl className="best-doors">
      {rows.map((r) => (
        <div key={r.door}>
          <dt>{doorName(r.door)}</dt>
          <dd>
            <span className="was">{pct(r.before)}</span>
            <span aria-hidden="true"> → </span>
            <span className="sr-only"> to </span>
            <strong>{pct(r.after)}</strong>
            <span className="best-doors-unit"> {unit === 'of 100' ? 'of 100' : `of 100 ${unit}`}</span>
          </dd>
        </div>
      ))}
    </dl>
  )
}

// ---- indoors: the same two steps, measured in floor watched -------------------------------------

function IndoorBest({ plan, preview, onPreview, heading }) {
  const { best_existing: best, more_cameras: more, now, final } = plan
  const hasTurns = best.changes.length > 0
  const goal = Math.round(plan.goal * 100)
  const stages = [
    { label: 'Today', value: now.watched_frac },
    { label: hasTurns ? 'After turning cameras' : 'Turning cameras', value: best.watched_after, note: hasTurns ? null : 'no change' },
  ]
  if (more.needed) {
    const n = more.additions.length
    stages.push({ label: n ? `After adding ${n} ${n === 1 ? 'camera' : 'cameras'}` : 'Adding cameras', value: more.watched_after, note: n ? null : 'no change' })
  }
  const left = final.blind_spots

  return (
    <section className="best" aria-labelledby="best-heading">
      {heading}
      <p className="muted best-intro">
        The most floor we can get watched. Turn the cameras you have first, then add cameras only if the floor is still
        not covered. Figures are the share of the open floor with at least a 1 in 4 chance of being seen.
      </p>

      <ol className="ledger" aria-label="Floor watched at each step">
        {stages.map((s, i) => (
          <li key={s.label} className={i === stages.length - 1 ? 'ledger-step is-last' : 'ledger-step'}>
            <span className="ledger-label">{s.label}</span>
            <span className="ledger-number">{pct(s.value)}<span className="ledger-unit">%</span></span>
            <span className="ledger-door">{s.note ?? 'of the floor watched'}</span>
          </li>
        ))}
      </ol>

      <div className="best-steps">
        <article className="best-step">
          <h3 className="best-step-title">With the cameras you have</h3>
          <p className="best-sentence">{best.sentence}</p>
          {hasTurns && (
            <button className="button" aria-pressed={preview === 'best'} onClick={() => onPreview('best')}>
              {preview === 'best' ? 'Remove preview' : 'Preview on the map'}
            </button>
          )}
        </article>

        <article className="best-step">
          <h3 className="best-step-title">Do you need more cameras?</h3>
          <p className="best-sentence">
            {!more.needed
              ? 'No.'
              : more.additions.length === 0
                ? 'More cameras would not help much.'
                : more.reached_target
                  ? `Yes. Add ${more.additions.length} ${more.additions.length === 1 ? 'camera' : 'cameras'}.`
                  : `Add ${more.additions.length} ${more.additions.length === 1 ? 'camera' : 'cameras'}. That gets close, not all the way.`}
          </p>

          {more.additions.length > 0 && (
            <ul className="additions">
              {more.additions.map((a) => (
                <li key={a.name}>
                  <strong>{a.name}</strong> in the {a.where}, facing {a.compass}.
                  <span className="muted small"> At {a.position[0]} m, {a.position[1]} m on the plan.</span>
                </li>
              ))}
            </ul>
          )}

          {!more.needed && <p className="muted">After turning the cameras you have, at least {goal}% of the floor is watched.</p>}
          {more.needed && more.additions.length > 0 && (
            <p className="muted">
              {pct(more.watched_before)}% → <strong>{pct(more.watched_after)}%</strong> of the floor watched.
              {left.length > 0 && ' What is left is hidden behind furniture or in corners.'}
            </p>
          )}

          {more.additions.length > 0 && (
            <>
              <p className="muted small">
                Spots are on open floor beside a wall, never on furniture. Each new camera is 110° wide and sees 12 m.
                A camera on a wall that is in the way of a doorway or a window frame may need moving a little.
              </p>
              <button className="button" aria-pressed={preview === 'more'} onClick={() => onPreview('more')}>
                {preview === 'more' ? 'Remove preview' : 'Preview on the map'}
              </button>
            </>
          )}

          <Doors rows={more.needed ? more.doors : best.doors} unit="reach it unseen" />
          <p className="muted small">Intruder figures are from 200 intruders the search never saw.</p>
        </article>
      </div>
    </section>
  )
}

export default function BestSetup({ plan, preview, onPreview, anyOffline }) {
  const heading = <h2 id="best-heading" className="section-heading">Best setup</h2>

  if (plan.status === 'loading') {
    return (
      <section className="best" aria-labelledby="best-heading" aria-busy="true">
        {heading}
        <p className="muted">Trying thousands of camera layouts. The first search takes a few seconds, longer on a big plan.</p>
      </section>
    )
  }
  if (plan.status === 'error') {
    return (
      <section className="best" aria-labelledby="best-heading">
        {heading}
        <p className="problem" role="alert">{plan.message}</p>
      </section>
    )
  }

  if (plan.data.mode === 'indoor') {
    return <IndoorBest plan={plan.data} preview={preview} onPreview={onPreview} heading={heading} />
  }

  const { best_existing: best, more_cameras: more, now, final } = plan.data
  const today = worst(now)
  const afterTurn = worst(best.doors.map((d) => ({ door: d.door, rate: d.after })))
  const afterAdd = worst(final)
  const target = Math.round(plan.data.target * 100)
  const hasTurns = best.changes.length > 0

  const stages = [
    { label: 'Today', w: today },
    { label: hasTurns ? 'After turning cameras' : 'Turning cameras', w: afterTurn, note: hasTurns ? null : 'no change' },
  ]
  if (more.needed) {
    const n = more.additions.length
    stages.push({ label: n ? `After adding ${n} ${n === 1 ? 'camera' : 'cameras'}` : 'Adding cameras', w: afterAdd, note: n ? null : 'no change' })
  }

  return (
    <section className="best" aria-labelledby="best-heading">
      {heading}
      <p className="muted best-intro">
        The fewest intruders we can get in. Turn the cameras you have first, then add cameras only if a
        door is still exposed. Figures are intruders out of 100 who get in unseen.
      </p>

      <ol className="ledger" aria-label="Most exposed door at each step">
        {stages.map((s, i) => (
          <li key={s.label} className={i === stages.length - 1 ? 'ledger-step is-last' : 'ledger-step'}>
            <span className="ledger-label">{s.label}</span>
            <span className="ledger-number">{pct(s.w.rate)}</span>
            <span className="ledger-door">
              {s.note ?? (s.w.rate === 0 ? 'every door at 0' : `${doorName(s.w.door).toLowerCase()}, the most exposed`)}
            </span>
          </li>
        ))}
      </ol>

      <div className="best-steps">
        {/* ------------------------------------------------ step 1 */}
        <article className="best-step">
          <h3 className="best-step-title">With the cameras you have</h3>
          <p className="best-sentence">
            {hasTurns
              ? `Turn ${best.changes.map((c) => `${c.camera} ${c.degrees}° ${c.direction}`).join(' and ')}.`
              : 'They are already pointed about as well as they can be.'}
          </p>
          <Doors rows={best.doors} />
          <p className="muted small">
            Checked on 200 intruders the search never saw.{anyOffline ? ' Worked out with every camera working.' : ''}
          </p>
          {hasTurns && (
            <button className="button" aria-pressed={preview === 'best'} onClick={() => onPreview('best')}>
              {preview === 'best' ? 'Remove preview' : 'Preview on the map'}
            </button>
          )}
        </article>

        {/* ------------------------------------------------ step 2 */}
        <article className="best-step">
          <h3 className="best-step-title">Do you need more cameras?</h3>
          <p className="best-sentence">
            {!more.needed
              ? 'No.'
              : more.additions.length === 0
                ? 'More cameras would not help much.'
                : more.reached_target
                  ? `Yes. Add ${more.additions.length} ${more.additions.length === 1 ? 'camera' : 'cameras'}.`
                  : `Add ${more.additions.length} ${more.additions.length === 1 ? 'camera' : 'cameras'}, but it is still not enough.`}
          </p>

          {more.additions.length > 0 && (
            <ul className="additions">
              {more.additions.map((a) => (
                <li key={a.name}>
                  <strong>{a.name}</strong> on the {a.where.replace(' of the house', ' side of the house')}, facing {a.compass}.
                  <span className="muted small"> At {a.position[0]} m, {a.position[1]} m on the plan.</span>
                </li>
              ))}
            </ul>
          )}

          {more.needed ? (
            <Doors rows={more.doors} />
          ) : (
            <p className="muted">
              After turning the cameras you have, every door lets in {target} or fewer of 100 intruders.
            </p>
          )}

          {more.needed && more.additions.length > 0 && !more.reached_target && (
            <p className="muted">Look at lighting, fencing or locks on the exposed door as well.</p>
          )}
          {more.needed && more.additions.length === 0 && (
            <p className="muted">Look at lighting, fencing or locks on the exposed door instead.</p>
          )}

          {more.additions.length > 0 && (
            <>
              <p className="muted small">
                Spots assume a camera can be fixed to a house wall, an eave or a fence post at a property corner.
                Each new camera is 90° wide and sees 9 m, like your existing ones.
              </p>
              <button className="button" aria-pressed={preview === 'more'} onClick={() => onPreview('more')}>
                {preview === 'more' ? 'Remove preview' : 'Preview on the map'}
              </button>
            </>
          )}
        </article>
      </div>
    </section>
  )
}
