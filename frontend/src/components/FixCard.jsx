// FixCard.jsx - "what is the smallest change that helps?"
//
// The backend searches every camera and every turn of up to 45 degrees,
// picks the best one using one set of intruders, then CHECKS it on a
// second set the search never saw. The numbers shown here are from that
// second set, so a fix that only looked good by luck would not pass.
//
// States handled: loading, error, nothing to fix, no turn helps, a turn helps.

import { doorWords, pct } from '../lib/format'

export default function FixCard({ door, state, previewing, onTogglePreview, anyOffline }) {
  const heading = <h2 className="section-heading">Smallest fix</h2>

  if (state.status === 'loading') {
    return (
      <section className="card fix" aria-busy="true">
        {heading}
        <p className="muted">Searching every camera and every turn…</p>
      </section>
    )
  }
  if (state.status === 'error') {
    return (
      <section className="card fix">
        {heading}
        <p className="problem" role="alert">{state.message}</p>
      </section>
    )
  }

  const fix = state.data.fix

  if (fix.change_type === 'none_needed') {
    return (
      <section className="card fix">
        {heading}
        <p className="fix-main">Nothing to fix here.</p>
        <p className="muted">No simulated intruder gets through to the {doorWords(door)} with every camera working.</p>
      </section>
    )
  }

  if (fix.change_type === 'none') {
    return (
      <section className="card fix">
        {heading}
        <p className="fix-main">Turning a camera will not help here.</p>
        <p className="muted">{fix.description}</p>
      </section>
    )
  }

  return (
    <section className="card fix">
      {heading}
      <p className="fix-main">
        Turn camera {fix.camera} by {fix.degrees}° {fix.direction}.
      </p>
      <p className="fix-numbers">
        The {doorWords(door)} goes from <strong>{pct(fix.held_out_before)}</strong> to{' '}
        <strong>{pct(fix.held_out_after)}</strong> of 100 getting in.
      </p>
      <p className="muted">
        {fix.genuinely_improved
          ? 'Checked on 200 intruders the search never saw.'
          : 'This turn did not hold up on new intruders, so treat it with caution.'}
        {anyOffline ? ' Worked out with every camera working.' : ''}
      </p>
      <button className="button" aria-pressed={previewing} onClick={onTogglePreview}>
        {previewing ? 'Remove preview' : 'Preview on the map'}
      </button>
    </section>
  )
}
