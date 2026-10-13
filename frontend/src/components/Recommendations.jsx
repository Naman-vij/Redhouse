// Recommendations.jsx - "Analysis & recommendations": the short version of the best setup.
//
//   1. how exposed the doors are today
//   2. whether a new camera is worth adding, and where (a preview, never a purchase)
//   3. the best angle for each camera you already have
//
// The long version, with every door's before-and-after, is in BestSetup below. Every figure and sentence
// here comes from the same plan answer (backend routes/plan.py).

import { compass, doorName, pct } from '../lib/format'
import Icon from './Icon'

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`

export default function Recommendations({ plan, cameras, headings, doors, indoor, floor, preview, onPreview }) {
  if (plan.status === 'loading') {
    return (
      <section className="card" id="recommendations" aria-busy="true" aria-labelledby="reco-title">
        <h2 id="reco-title" className="card-title">Analysis &amp; recommendations</h2>
        <p className="card-sub">Trying thousands of camera layouts. The first search takes a few seconds, longer on a big plan.</p>
      </section>
    )
  }
  if (plan.status === 'error') {
    return (
      <section className="card" id="recommendations" aria-labelledby="reco-title">
        <h2 id="reco-title" className="card-title">Analysis &amp; recommendations</h2>
        <p className="problem" role="alert">{plan.message}</p>
      </section>
    )
  }

  const { best_existing: best, more_cameras: more } = plan.data
  const changes = Object.fromEntries(best.changes.map((c) => [c.camera, c]))
  const adds = more.additions

  // today's exposure, from the plan's own "now" figures (the cameras as installed)
  const nowRows = indoor ? plan.data.now_rates.map((r) => ({ door: r.door, rate: r.rate })) : plan.data.now
  const worst = nowRows.reduce((a, b) => (b.rate > a.rate ? b : a), nowRows[0])
  const exposed = nowRows.filter((r) => pct(r.rate) > 0)
  const blindNow = indoor ? floor?.blind_m2 : 0

  let alert
  if (indoor) {
    alert = blindNow > 0
      ? { tone: 'bad', title: `${blindNow} m² of the floor has no camera on it`, text: `${doorName(worst.door)}: ${pct(worst.rate)} of 100 intruders reach it unseen today.` }
      : { tone: 'good', title: 'No part of the open floor is blind', text: 'Every protected spot is watched.' }
  } else if (exposed.length > 0) {
    alert = {
      tone: 'bad',
      title: `${plural(exposed.length, 'door is', 'doors are')} not fully covered`,
      text: `${doorName(worst.door)} is the most exposed: ${pct(worst.rate)} of 100 get in unseen today.`,
    }
  } else {
    alert = { tone: 'good', title: 'Every door is well covered', text: 'No simulated intruder gets in unseen with every camera working.' }
  }

  const first = adds[0]
  const afterDoors = more.doors ?? []
  const afterWorst = afterDoors.length ? afterDoors.reduce((a, b) => (b.after > a.after ? b : a)) : null

  return (
    <section className="card" id="recommendations" aria-labelledby="reco-title">
      <h2 id="reco-title" className="card-title">Analysis &amp; recommendations</h2>

      <div className={`alert alert-${alert.tone}`}>
        <span className="alert-icon"><Icon name={alert.tone === 'bad' ? 'alert' : 'check'} size={18} /></span>
        <div>
          <strong>{alert.title}</strong>
          <p>{alert.text}</p>
        </div>
      </div>

      {more.needed && adds.length > 0 ? (
        <div className="reco-row">
          <span className="reco-icon"><Icon name="camera" size={20} /></span>
          <div className="reco-text">
            <strong>
              Add {plural(adds.length, 'camera', 'cameras')} <em className="tag-good">Recommended</em>
            </strong>
            <p>
              {first.name} {indoor ? `in the ${first.where}` : `on the ${first.where.replace(' of the house', ' side of the house')}`}, facing {first.compass}.
              {afterWorst && !indoor ? ` The most exposed door goes to ${pct(afterWorst.after)} of 100.` : ''}
              {indoor ? ` Floor watched: ${pct(more.watched_before)}% to ${pct(more.watched_after)}%.` : ''}
            </p>
          </div>
          <button className="button button-primary" aria-pressed={preview === 'more'} onClick={() => onPreview('more')}>
            {preview === 'more' ? 'Remove preview' : 'Preview'}
          </button>
        </div>
      ) : (
        <div className="reco-row">
          <span className="reco-icon"><Icon name="check" size={20} /></span>
          <div className="reco-text">
            <strong>{more.needed ? 'More cameras would not help much' : 'You do not need more cameras'}</strong>
            <p>
              {more.needed
                ? 'Look at lighting, fencing or locks on the exposed door instead.'
                : 'After turning the cameras you have, the doors are covered as well as they can be.'}
            </p>
          </div>
        </div>
      )}

      <div className="reco-head">
        <h3>Best camera angles</h3>
        {best.changes.length > 0 && (
          <button className="button button-small" aria-pressed={preview === 'best'} onClick={() => onPreview('best')}>
            {preview === 'best' ? 'Remove preview' : 'Preview'}
          </button>
        )}
      </div>
      <ul className="angles">
        {cameras.map((cam) => {
          const ch = changes[cam.name]
          return (
            <li key={cam.name}>
              <span className="angles-name">{cam.name}</span>
              <span className="angles-where">Facing {compass(headings[cam.name] ?? cam.heading_deg)}</span>
              <span className="angles-turn">{ch ? `${ch.degrees}° ${ch.direction}` : 'Keep as is'}</span>
              <span className={ch ? 'badge badge-turn' : 'badge badge-good'}>{ch ? 'Turn' : 'Optimal'}</span>
            </li>
          )
        })}
      </ul>
      <p className="card-note">Checked on 200 intruders the search never saw.</p>
    </section>
  )
}
