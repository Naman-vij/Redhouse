// Findings.jsx - the right-hand column's top half:
//   DoorTabs       pick a door; every door's result is visible at a glance
//   Exposure       one sentence + 100 dots that say the same thing
//   IntruderPicker who is trying to get in
//
// NOTE: the dots are a "unit chart". 100 intruders, each one a dot, so
// "60 of 100" can be SEEN instead of read. Filled red = got in unseen,
// hollow teal = was seen first. The shape differs as well as the colour,
// so it still reads for colour-blind viewers.

import { INTRUDERS, doorName, doorWords, pct, humanise } from '../lib/format'

export function DoorTabs({ doors, active, onSelect, indoor = false }) {
  return (
    <div className="door-tabs" role="tablist" aria-label={indoor ? 'Protected spot to inspect' : 'Door to inspect'}>
      {doors.map((d) => (
        <button
          key={d.door}
          role="tab"
          aria-selected={d.door === active}
          className={d.door === active ? 'door-tab is-active' : 'door-tab'}
          onClick={() => onSelect(d.door)}
        >
          <span className="door-tab-name">{doorName(d.door)}</span>
          <span className="door-tab-number">{pct(d.rate)}</span>
          <span className="door-tab-unit">{indoor ? 'of 100 reach it unseen' : 'of 100 get in'}</span>
        </button>
      ))}
    </div>
  )
}

export function Exposure({ door, rate, summary, indoor = false }) {
  const n = pct(rate)
  const headline =
    n === 0
      ? `No simulated intruder reaches the ${doorWords(door)} unseen.`
      : `${n} of 100 simulated intruders reach the ${doorWords(door)} without being seen.`

  return (
    <section className="exposure" aria-labelledby="exposure-heading">
      <h2 id="exposure-heading" className="sr-only">Result for the {doorWords(door)}</h2>
      <p className="headline">{headline}</p>

      {/* 100 dots: the first n are the ones that got in */}
      <div className="dots" role="img" aria-label={`${n} of 100 intruders ${indoor ? 'reach it' : 'get in'} unseen`}>
        {Array.from({ length: 100 }, (_, i) => (
          <span key={i} className={i < n ? 'dot dot-in' : 'dot dot-seen'} />
        ))}
      </div>
      <p className="dots-key">
        <span className="dot dot-in" /> {indoor ? 'gets there unseen' : 'gets in unseen'}
        <span className="dot dot-seen" /> is seen first
      </p>

      {/* only shown when a camera is offline: what that does to the doors */}
      {summary && !summary.startsWith('All ') && <p className="state-note">{humanise(summary)}</p>}
    </section>
  )
}

export function IntruderPicker({ value, onChange }) {
  return (
    <fieldset className="intruders">
      <legend>Who is trying to get in</legend>
      {INTRUDERS.map((it) => (
        <label key={it.id} className={it.id === value ? 'intruder is-active' : 'intruder'}>
          <input
            type="radio"
            name="intruder"
            value={it.id}
            checked={it.id === value}
            onChange={() => onChange(it.id)}
          />
          <span className="intruder-name">{it.name}</span>
          <span className="intruder-blurb">{it.blurb}</span>
        </label>
      ))}
    </fieldset>
  )
}
