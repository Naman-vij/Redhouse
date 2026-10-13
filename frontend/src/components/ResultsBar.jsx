// ResultsBar.jsx - the bar that stays at the bottom of the page: who is trying to get in, and what the
// simulation says right now. The numbers update by themselves, so there is no "run" button.

import { INTRUDERS, doorName, pct } from '../lib/format'
import Icon from './Icon'

export default function ResultsBar({ archetype, onArchetype, door, rate, indoor, cameras, starts, changed, onReset, reportHref }) {
  const unseen = pct(rate)
  return (
    <div className="results-bar" role="region" aria-label="Simulation controls and results">
      <div className="rb-group">
        <span className="rb-icon"><Icon name="chart" size={22} /></span>
        <div>
          <strong className="rb-title">Simulation</strong>
          <span className="rb-sub">{starts} simulated intruders</span>
        </div>
      </div>

      <label className="rb-field">
        <span>Who is trying to get in</span>
        <select value={archetype} onChange={(e) => onArchetype(e.target.value)}>
          {INTRUDERS.map((it) => (
            <option key={it.id} value={it.id}>{it.name}</option>
          ))}
        </select>
      </label>

      <dl className="rb-results" aria-label={`Results for the ${door ? doorName(door).toLowerCase() : 'door'}`}>
        <div><dt><i className="dot dot-seen" /> Seen</dt><dd>{100 - unseen}</dd></div>
        <div><dt><i className="dot dot-in" /> {indoor ? 'Unseen' : 'Got in'}</dt><dd>{unseen}</dd></div>
        <div><dt><Icon name="camera" size={14} /> Cameras</dt><dd>{cameras}</dd></div>
      </dl>

      <div className="rb-actions">
        <button className="button" onClick={onReset} disabled={!changed}>
          <Icon name="reset" size={16} /> Reset changes
        </button>
        {reportHref && (
          <a className="button button-primary" href={reportHref} download>
            <Icon name="download" size={16} /> Download report
          </a>
        )}
      </div>
    </div>
  )
}
