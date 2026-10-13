// format.js - small helpers that turn data into the words and numbers on screen.

/** 0.6 -> 60. Rounded ONCE so every place on the page shows the same figure. */
export const pct = (x) => Math.round(x * 100)

/** "front_door" -> "Front door" */
export function doorName(id) {
  const words = id.replace(/_/g, ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

/** "front_door" -> "front door" (for the middle of a sentence) */
export const doorWords = (id) => id.replace(/_/g, ' ')

/**
 * The three kinds of intruder the simulator knows, in plain words.
 * `id` must match the names in the backend's attackers.py.
 */
export const INTRUDERS = [
  { id: 'opportunist', name: 'Opportunist', blurb: 'Takes the shortest way in.' },
  { id: 'camera_avoider', name: 'Camera avoider', blurb: 'Detours around anything a camera watches.' },
  { id: 'side_rear_entry', name: 'Side and rear entry', blurb: 'Heads for the least-watched door.' },
]

/** "2026-10-12T05:37:21Z" -> "4:07 pm" in the viewer's own time zone. */
export function clock(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? ''
    : d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

/** A Date -> "4:07:12 pm" */
export function clockSeconds(date) {
  return date ? date.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit', second: '2-digit' }) : ''
}

/** The signed angle the backend wants for a fix: counter-clockwise is positive. */
export function fixAngle(fix) {
  return fix.direction === 'counter-clockwise' ? fix.degrees : -fix.degrees
}

/** Backend sentences mention doors as "side_door"; show them as "side door". */
export const humanise = (text) => text.replace(/\b([a-z]+)_door\b/g, '$1 door')

const COMPASS = ['east', 'north-east', 'north', 'north-west', 'west', 'south-west', 'south', 'south-east']

/** 90 -> "north". 0 = east, 90 = north (the map's own convention). */
export const compass = (deg) => COMPASS[Math.round((((deg % 360) + 360) % 360) / 45) % 8]

/** Any angle -> between -180 and 180. */
export function wrap180(deg) {
  const d = ((deg % 360) + 360) % 360
  return d > 180 ? d - 360 : d
}

/** 45 -> "45° counter-clockwise", -30 -> "30° clockwise", 0 -> "not turned". */
export function turnText(angle) {
  if (!angle) return 'not turned'
  return `${Math.abs(angle)}° ${angle > 0 ? 'counter-clockwise' : 'clockwise'}`
}
