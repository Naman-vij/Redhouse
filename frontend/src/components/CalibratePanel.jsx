// CalibratePanel.jsx - "See what this camera really sees".
//
// Today every camera's view is a guess: a wedge worked out from where it is, which way it points and how wide
// it sees. This panel replaces the guess for ONE camera with a measurement:
//   1. get a picture from that camera: its latest saved picture from Ring, or a screenshot you upload
//   2. click a spot on the ground in the picture, then the same spot on the plan. Four spots that are spread
//      out are enough; a fifth is used to check the other four
//   3. the server works out exactly which ground the picture shows (engine/calibration.py) and draws it on
//      the plan. Save it, and every number on the page uses it
//   4. optionally, the same spots also say where the camera is standing and which way it points, so the camera
//      can be placed and aimed from the picture. That needs the camera's lens angle, which Ring does not give,
//      so it is assumed (adjustable) and the answer says how far it could be off
//
// PRIVACY: the picture is never stored. An uploaded one stays in this browser; a Ring one passes through our
// server on its way here and is not kept. Only the clicked spots (numbers) are saved with the site.

import { useEffect, useMemo, useRef, useState } from 'react'
import { previewCalibration, saveCalibration } from '../api'
import { useDebounced } from '../lib/useDebounced'
import SAMPLES from '../lib/samplePictures.json'
import Icon from './Icon'

const MAX_SPOTS = 8

function when(stamp) {
  if (!stamp) return ''
  const d = /^\d+$/.test(stamp) ? new Date(Number(stamp)) : new Date(stamp)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

/** The plan, small: property, buildings, cameras, the matched spots, and the measured view once there is one. */
function PlanPane({ site, camera, spots, view, placed, onPick }) {
  const svgRef = useRef(null)
  const xs = site.boundary.map((p) => p[0])
  const ys = site.boundary.map((p) => p[1])
  const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys)
  const span = Math.max(maxx - minx, maxy - miny)
  const pad = span * 0.06
  const Y = (y) => maxy - y
  const pts = (list) => list.map(([x, y]) => `${x},${Y(y)}`).join(' ')
  const k = span / 26 // marker size, in metres, for a plan about 24 m across

  function click(e) {
    const svg = svgRef.current
    const ctm = svg?.getScreenCTM()
    if (!ctm) return
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(ctm.inverse())
    onPick([Math.round(p.x * 100) / 100, Math.round((maxy - p.y) * 100) / 100])
  }

  return (
    <svg
      ref={svgRef}
      className="cal-plan"
      viewBox={`${minx - pad} ${-pad} ${maxx - minx + 2 * pad} ${maxy - miny + 2 * pad}`}
      onClick={click}
      role="img"
      aria-label={`Plan. Click the same spot you clicked in the picture. Camera ${camera.name} is highlighted.`}
    >
      <polygon className="cal-ground" points={pts(site.boundary)} />
      {site.obstacles.map((o, i) => <polygon key={i} className="cal-house" points={pts(o)} />)}
      {view && <polygon className="cal-view" points={pts(view)} />}
      {site.cameras.filter((c) => !c.proposed).map((c) => {
        const moved = placed && c.name === camera.name
        const [cx, cy] = moved ? placed.position : c.position
        return (
          <g key={c.name} className={c.name === camera.name ? 'cal-cam is-this' : 'cal-cam'}>
            {moved && <circle className="cal-was" cx={c.position[0]} cy={Y(c.position[1])} r={0.5 * k} />}
            {moved && placed.uncertainty_m > 0 && (
              <circle className="cal-unsure" cx={cx} cy={Y(cy)} r={Math.max(placed.uncertainty_m, 0.3)} />
            )}
            <circle cx={cx} cy={Y(cy)} r={(c.name === camera.name ? 0.55 : 0.4) * k} />
            <text x={cx} y={Y(cy) + 1.5 * k} fontSize={1.0 * k} textAnchor="middle">{c.name}</text>
          </g>
        )
      })}
      {spots.map((s, i) => s.ground && (
        <g key={i} className={i === spots.length - 1 && spots.length >= 5 ? 'cal-spot is-check' : 'cal-spot'}>
          <circle cx={s.ground[0]} cy={Y(s.ground[1])} r={0.6 * k} />
          <text x={s.ground[0]} y={Y(s.ground[1])} fontSize={0.8 * k} textAnchor="middle" dominantBaseline="central">{i + 1}</text>
        </g>
      ))}
    </svg>
  )
}

/** The camera's picture, with the matched spots on it. Clicks are sent as 0..1 across and down. */
function PicturePane({ picture, spots, onPick, onAspect }) {
  const imgRef = useRef(null)
  function click(e) {
    const r = imgRef.current?.getBoundingClientRect()
    if (!r) return
    const u = (e.clientX - r.left) / r.width
    const v = (e.clientY - r.top) / r.height
    if (u < 0 || u > 1 || v < 0 || v > 1) return
    onPick([Math.round(u * 10000) / 10000, Math.round(v * 10000) / 10000])
  }
  return (
    <div className="cal-photo" onClick={click}>
      <img
        ref={imgRef}
        src={picture.url}
        alt="The camera's picture. Click a spot on the ground."
        draggable={false}
        onLoad={(e) => onAspect(e.currentTarget.naturalWidth / e.currentTarget.naturalHeight)}
      />
      {spots.map((s, i) => s.img && (
        <span
          key={i}
          className={i === spots.length - 1 && spots.length >= 5 ? 'cal-dot is-check' : 'cal-dot'}
          style={{ left: `${s.img[0] * 100}%`, top: `${s.img[1] * 100}%` }}
        >
          {i + 1}
        </span>
      ))}
    </div>
  )
}

export default function CalibratePanel({ site, scene, camera, ring, ringLinked, onClose, onSaved }) {
  const [picture, setPicture] = useState(null) // { url, capturedAt, source }
  const [fetching, setFetching] = useState(false)
  const [problem, setProblem] = useState(null)
  const [spots, setSpots] = useState([]) // [{ img: [u, v] | null, ground: [x, y] | null }]
  const [preview, setPreview] = useState({ status: 'idle' })
  const [saving, setSaving] = useState(false)
  const [place, setPlace] = useState(false) // also work out where the camera is, from the picture
  const [lens, setLens] = useState(100) // assumed horizontal field of view of the lens, in degrees
  const [aspect, setAspect] = useState(16 / 9) // the picture's width / height
  const fileRef = useRef(null)

  // Let go of the picture when it is replaced or the panel closes.
  useEffect(() => () => picture && URL.revokeObjectURL(picture.url), [picture])

  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function fromRing() {
    setFetching(true)
    setProblem(null)
    try {
      const shot = await ring.picture(camera.name)
      setPicture({ ...shot, source: 'ring' })
      setSpots([])
    } catch (err) {
      setProblem(err.message)
    } finally {
      setFetching(false)
    }
  }

  // The sample picture is drawn from the demo house, so it only fits a demo camera that is still where the demo puts it.
  const sample = SAMPLES[camera.name]
  const sampleFits =
    !!sample && Math.abs(camera.position[0] - sample.camera[0]) < 0.05 && Math.abs(camera.position[1] - sample.camera[1]) < 0.05

  function fromSample() {
    setProblem(null)
    setPicture({ url: sample.picture, capturedAt: null, source: 'upload', sample: true })
    setSpots(sample.spots.map((s) => ({ img: s.img, ground: s.ground })))
    setPlace(false)
    setLens(sample.lens_deg)
  }

  function fromFile(e) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (!/^image\/(png|jpeg)$/.test(file.type)) return setProblem('Choose a PNG or JPEG picture.')
    setProblem(null)
    setPicture({ url: URL.createObjectURL(file), capturedAt: null, source: 'upload' })
    setSpots([])
  }

  // A click on either side fills the spot being worked on; once both halves are there, the next click starts a new one.
  function pick(side, point) {
    setSpots((list) => {
      const last = list[list.length - 1]
      if (last && !(last.img && last.ground)) {
        return [...list.slice(0, -1), { ...last, [side]: point }]
      }
      if (list.length >= MAX_SPOTS) return list
      return [...list, { img: null, ground: null, [side]: point }]
    })
  }

  const complete = spots.filter((s) => s.img && s.ground)
  const ready = complete.length >= 4 && complete.length === spots.length
  const body = useMemo(
    () =>
      ready
        ? {
            camera: camera.name,
            image_points: complete.map((s) => s.img),
            ground_points: complete.map((s) => s.ground),
            source: picture?.source ?? 'upload',
            captured_at: picture?.capturedAt ?? null,
            ...(place ? { place_camera: true, aspect: Math.round(aspect * 1000) / 1000, lens_deg: lens } : {}),
          }
        : null,
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [ready, JSON.stringify(complete), picture, place, lens, aspect],
  )
  const settled = useDebounced(body ? JSON.stringify(body) : '', 300)

  useEffect(() => {
    if (!settled) return setPreview({ status: 'idle' })
    const ctrl = new AbortController()
    setPreview((p) => ({ ...p, status: 'loading' }))
    previewCalibration(site, JSON.parse(settled), ctrl.signal)
      .then((data) => setPreview({ status: 'ok', data }))
      .catch((err) => err.name !== 'AbortError' && setPreview({ status: 'error', message: err.message }))
    return () => ctrl.abort()
  }, [settled, site])

  async function save() {
    if (!body) return
    setSaving(true)
    setProblem(null)
    try {
      onSaved(await saveCalibration(site, body))
    } catch (err) {
      setProblem(err.message)
      setSaving(false)
    }
  }

  const last = spots[spots.length - 1]
  const n = spots.length
  let step
  if (!picture) step = 'Get a picture from this camera first.'
  else if (last && !last.ground) step = `Spot ${n}: now click the same place on the plan.`
  else if (last && !last.img) step = `Spot ${n}: now click the same place in the picture.`
  else if (n < 4) step = `Spot ${n + 1}: click a spot on the ground in the picture, such as a fence corner or the edge of a path.`
  else if (n === 4) step = 'Good. Now add a 5th spot: it checks the other four, and the view cannot be saved without it.'
  else if (n < MAX_SPOTS) step = 'The last spot is the check spot. Save when the view looks right.'
  else step = `That is the most spots (${MAX_SPOTS}). Save when the view looks right.`

  const data = preview.status === 'ok' ? preview.data : null

  return (
    <div className="cal-backdrop" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <section className="cal" role="dialog" aria-modal="true" aria-labelledby="cal-title">
        <header className="cal-head">
          <div>
            <h2 id="cal-title">What camera {camera.name} really sees</h2>
            <p className="muted">
              Match spots on the ground in its picture to the same spots on the plan. RedHouse then works out the exact
              ground the camera covers, instead of guessing it from the direction and angle.
            </p>
          </div>
          <button className="button button-quiet" onClick={onClose}>Close</button>
        </header>

        <div className="cal-source">
          <button
            className="button button-primary"
            onClick={fromRing}
            disabled={!ringLinked || fetching}
            title={ringLinked ? undefined : 'Connect Ring and link this camera to a Ring device first.'}
          >
            <Icon name="ring" size={17} /> {fetching ? 'Getting picture…' : 'Get latest picture from Ring'}
          </button>
          <button className="button" onClick={() => fileRef.current?.click()}>
            <Icon name="upload" size={17} /> Upload a screenshot
          </button>
          {sampleFits && (
            <button className="button" onClick={fromSample}>
              <Icon name="camera" size={17} /> Try the sample picture
            </button>
          )}
          <input ref={fileRef} type="file" accept="image/png,image/jpeg" hidden onChange={fromFile} />
          {!ringLinked && <span className="muted small">To use Ring, connect it and link this camera first.</span>}
        </div>
        {picture && (
          <p className="muted small cal-note">
            {picture.source === 'ring'
              ? `Ring's latest saved picture${picture.capturedAt ? `, taken ${when(picture.capturedAt)}` : ''}. Ring gives the last picture the camera saved (from a motion event or live view), not a new one.`
              : picture.sample
                ? 'A sample picture drawn from the demo house, not a real camera. Its five spots are already matched; use Clear spots to match your own.'
                : 'Your picture.'}{' '}
            It is not stored anywhere; only the spots you click are saved.
          </p>
        )}
        {problem && <p className="problem" role="alert">{problem}</p>}

        <p className="cal-step" aria-live="polite"><strong>{step}</strong></p>

        <div className="cal-panes">
          <div className="cal-pane">
            <h3>Camera picture</h3>
            {picture ? (
              <PicturePane picture={picture} spots={spots} onPick={(p) => pick('img', p)} onAspect={setAspect} />
            ) : (
              <div className="cal-empty">No picture yet</div>
            )}
          </div>
          <div className="cal-pane">
            <h3>Plan</h3>
            <PlanPane
              site={scene.site}
              camera={camera}
              spots={spots}
              view={data?.ground}
              placed={data?.pose}
              onPick={(p) => picture && pick('ground', p)}
            />
          </div>
        </div>

        <div className="cal-place">
          <label className="cal-check">
            <input type="checkbox" checked={place} onChange={(e) => setPlace(e.target.checked)} />
            <span>
              <strong>Also place and aim the camera from the picture</strong>
              <span className="muted small"> Works out where it is mounted, how high, and which way it points, so you do not have to place it by hand.</span>
            </span>
          </label>
          {place && (
            <label className="cal-lens">
              <span>Lens width: <strong>{lens}°</strong> <span className="muted small">(a typical home camera is 90 to 120°; Ring does not say)</span></span>
              <input type="range" min="60" max="140" step="5" value={lens} onChange={(e) => setLens(Number(e.target.value))} />
            </label>
          )}
        </div>

        <div className="cal-result" aria-live="polite">
          {preview.status === 'loading' && <p className="muted">Working out the view…</p>}
          {preview.status === 'error' && <p className="problem">{preview.message}</p>}
          {data && (
            <>
              <p>
                <Icon name="check" size={17} /> Camera {camera.name} sees about <strong>{Math.round(data.area_m2)} m²</strong> of ground,
                facing {Math.round(data.heading_deg)}° with a {Math.round(data.fov_deg)}° spread.{' '}
                {data.checked
                  ? `The check spot landed ${data.max_error_m.toFixed(1)} m from where you marked it.`
                  : 'Not checked yet: add a 5th spot to test it.'}
              </p>
              {data.pose && (
                <p>
                  <Icon name="pin" size={17} /> Camera {camera.name} is placed at <strong>{data.pose.position[0].toFixed(1)}, {data.pose.position[1].toFixed(1)} m</strong>,{' '}
                  {data.pose.height_m.toFixed(1)} m up, tilted {Math.round(data.pose.tilt_deg)}° down, facing {Math.round(data.pose.heading_deg)}°.
                  {data.pose.uncertainty_m >= 0.3 ? ` Give or take about ${data.pose.uncertainty_m.toFixed(1)} m, because the lens width is assumed.` : ''}
                </p>
              )}
              {data.warnings.filter((w) => !w.includes('5th')).map((w) => (
                <p key={w} className="cal-warn"><Icon name="alert" size={16} /> {w}</p>
              ))}
            </>
          )}
        </div>

        <footer className="cal-actions">
          <button className="button button-quiet" onClick={() => setSpots((s) => s.slice(0, -1))} disabled={!n}>
            Undo last spot
          </button>
          <button className="button button-quiet" onClick={() => setSpots([])} disabled={!n}>Clear spots</button>
          <span className="cal-spacer" />
          {data && !data.checked && <span className="muted small">Add a 5th spot to save.</span>}
          <button className="button button-primary" onClick={save} disabled={!data || !data.checked || saving || preview.status !== 'ok'}>
            {saving ? 'Saving…' : `${place ? 'Use this view and position' : 'Use this view'}${site ? '' : ' (saves a copy of the demo)'}`}
          </button>
        </footer>
      </section>
    </div>
  )
}
