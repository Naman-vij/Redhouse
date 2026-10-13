// UploadWizard.jsx - add your own plan.
//
// You give us a drawing (a floor plan, or a site plan with a house on it) and click a handful of
// points on it. We read the walls from the picture ourselves; the clicks tell us what the picture
// cannot: how big it is, what is inside, where people come in, what must stay safe, and where the
// cameras are and which way they point.
//
//   1 plan       choose the picture (PNG, JPEG or PDF), name it, say indoors or outdoors
//   2 scale      click two points a known distance apart
//   3 inside     click on open floor / open ground
//   4 entrances  (indoors) the doorways from outside      | doors (outdoors) the doors on the house
//   5 protected  (indoors) what must stay safe
//   6 cameras    click where each camera is, then where it points
//   7 check      we read the plan and show what we found. Nothing is kept until you save.
//
// Everything you click is in the drawing's own pixels: the picture we upload is the picture you
// clicked on. The picture itself is not kept on the server, only the outline we read from it.

import { useEffect, useMemo, useState } from 'react'
import { checkSite, createSite, previewPlan } from './api'
import PlanCanvas from './components/PlanCanvas'
import Shell from './components/Shell'
import { SAMPLE_LAB } from './lib/sampleLab'
import { recentSites, rememberSite } from './lib/sites'

const MAX_SIDE = 3000 // longest side we send, in pixels
const MAX_BYTES = 7 * 1024 * 1024 // the server's limit is 8 MB
const FOV_CHOICES = [90, 110, 130]

const distance = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1])
const headingOf = (from, to) => (Math.atan2(-(to[1] - from[1]), to[0] - from[0]) * 180) / Math.PI

/** Any picture -> a PNG (or JPEG if that is smaller) no larger than MAX_SIDE, on a white background. */
async function prepareImage(file) {
  const url = URL.createObjectURL(file)
  try {
    const img = await new Promise((resolve, reject) => {
      const el = new Image()
      el.onload = () => resolve(el)
      el.onerror = () => reject(new Error('That picture could not be opened. Use a PNG, JPEG or PDF.'))
      el.src = url
    })
    const scale = Math.min(1, MAX_SIDE / Math.max(img.naturalWidth, img.naturalHeight))
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.round(img.naturalWidth * scale))
    canvas.height = Math.max(1, Math.round(img.naturalHeight * scale))
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#fff' // a transparent background would otherwise read as black
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    let blob = await new Promise((r) => canvas.toBlob(r, 'image/png'))
    if (blob && blob.size > MAX_BYTES) blob = await new Promise((r) => canvas.toBlob(r, 'image/jpeg', 0.9))
    if (!blob || blob.size > MAX_BYTES) throw new Error('That picture is too large. Export it smaller, about 2000 pixels wide.')
    return { blob, width: canvas.width, height: canvas.height, name: file.name }
  } finally {
    URL.revokeObjectURL(url)
  }
}

function dataToBlob(b64, type) {
  const bin = atob(b64)
  const bytes = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i)
  return new Blob([bytes], { type })
}

export default function UploadWizard({ onCancel, onOpen }) {
  const [kind, setKind] = useState('indoor')
  const [name, setName] = useState('')
  const [image, setImage] = useState(null) // { blob, url, width, height }
  const [pdf, setPdf] = useState(null) // { file, pages, page }
  const [loadError, setLoadError] = useState(null)
  const [loading, setLoading] = useState(false)

  const [scale, setScale] = useState({ p1: null, p2: null, metres: '' })
  const [inside, setInside] = useState(null)
  const [entrances, setEntrances] = useState([])
  const [doors, setDoors] = useState([])
  const [assets, setAssets] = useState([])
  const [cameras, setCameras] = useState([])
  const [pendingCam, setPendingCam] = useState(null)
  const [advanced, setAdvanced] = useState({ gap_px: '', thin_px: '' })

  const [step, setStep] = useState('plan')
  const [check, setCheck] = useState({ status: 'idle' })
  const [nonce, setNonce] = useState(0)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  const indoor = kind === 'indoor'
  const steps = useMemo(
    () => (indoor
      ? [['plan', 'Plan'], ['scale', 'Scale'], ['inside', 'Inside'], ['entrances', 'Entrances'], ['assets', 'Protected spots'], ['cameras', 'Cameras'], ['check', 'Check']]
      : [['plan', 'Plan'], ['scale', 'Scale'], ['inside', 'Ground'], ['doors', 'Doors'], ['cameras', 'Cameras'], ['check', 'Check']]),
    [indoor],
  )
  const stepIndex = steps.findIndex(([id]) => id === step)

  useEffect(() => () => image?.url && URL.revokeObjectURL(image.url), [image])

  const ppm = scale.p1 && scale.p2 && Number(scale.metres) > 0 ? distance(scale.p1, scale.p2) / Number(scale.metres) : null

  // ---- the picture ------------------------------------------------------------------------------
  async function useFile(file, pageNo = 0) {
    setLoadError(null)
    setLoading(true)
    try {
      let source = file
      let pages = 1
      if (file.type === 'application/pdf' || /\.pdf$/i.test(file.name)) {
        const out = await previewPlan(file, pageNo)
        source = new File([dataToBlob(out.png_base64, 'image/png')], file.name.replace(/\.pdf$/i, '.png'), { type: 'image/png' })
        pages = out.pages
        setPdf({ file, pages, page: out.page })
      } else {
        setPdf(null)
      }
      const prepared = await prepareImage(source)
      setImage({ ...prepared, url: URL.createObjectURL(prepared.blob) })
      if (!name) setName(file.name.replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').slice(0, 60))
      // a different picture means the old clicks mean nothing
      setScale({ p1: null, p2: null, metres: '' })
      setInside(null)
      setEntrances([])
      setDoors([])
      setAssets([])
      setCameras([])
      setPendingCam(null)
      setCheck({ status: 'idle' })
    } catch (err) {
      setLoadError(err.message)
    } finally {
      setLoading(false)
    }
  }

  async function useSample() {
    setLoadError(null)
    setLoading(true)
    try {
      const res = await fetch(`${import.meta.env.BASE_URL}${SAMPLE_LAB.file}`)
      const blob = await res.blob()
      const prepared = await prepareImage(new File([blob], SAMPLE_LAB.file, { type: 'image/png' }))
      setPdf(null)
      setImage({ ...prepared, url: URL.createObjectURL(prepared.blob) })
      setKind(SAMPLE_LAB.kind)
      setName(SAMPLE_LAB.name)
      setScale(SAMPLE_LAB.scale)
      setInside(SAMPLE_LAB.inside)
      setEntrances(SAMPLE_LAB.entrances)
      setAssets(SAMPLE_LAB.assets)
      setDoors([])
      setCameras(SAMPLE_LAB.cameras)
      setPendingCam(null)
      setCheck({ status: 'idle' })
      setStep('check')
    } catch {
      setLoadError('The sample could not be loaded.')
    } finally {
      setLoading(false)
    }
  }

  // ---- what we send --------------------------------------------------------------------------------
  function settings() {
    const out = {
      name: name.trim() || (indoor ? 'My floor' : 'My property'),
      mode: kind,
      scale: { p1: scale.p1, p2: scale.p2, metres: Number(scale.metres) },
      inside,
      cameras: cameras.map((c) => ({ name: c.name, position: c.position, aim: c.aim, fov_deg: c.fov_deg, range_m: c.range_m, online: c.online })),
    }
    if (indoor) {
      out.entrances = entrances.map((e) => ({ name: e.name, point: e.point }))
      out.assets = assets.map((a) => ({ name: a.name, point: a.point }))
    } else {
      out.doors = doors.map((d) => ({ name: d.name, point: d.point }))
    }
    if (advanced.gap_px !== '') out.gap_px = Number(advanced.gap_px)
    if (advanced.thin_px !== '') out.thin_px = Number(advanced.thin_px)
    return out
  }

  // reading the plan, whenever we arrive at the last step (or ask to read it again)
  useEffect(() => {
    if (step !== 'check' || !image) return undefined
    const ctrl = new AbortController()
    setCheck({ status: 'loading' })
    checkSite(image.blob, settings(), ctrl.signal)
      .then((data) => setCheck({ status: 'ok', data }))
      .catch((err) => err.name !== 'AbortError' && setCheck({ status: 'error', message: err.message }))
    return () => ctrl.abort()
    // settings() reads the current state; `step` and `nonce` are the real triggers
  }, [step, nonce]) // eslint-disable-line react-hooks/exhaustive-deps

  async function save() {
    setSaving(true)
    setSaveError(null)
    try {
      const out = await createSite(image.blob, settings())
      rememberSite({ id: out.site_id, name: out.meta.name, mode: out.meta.mode })
      onOpen(out.site_id)
    } catch (err) {
      setSaveError(err.message)
      setSaving(false)
    }
  }

  // ---- what each step needs before "Next" works --------------------------------------------------
  const ready = {
    plan: !!image,
    scale: !!ppm && ppm > 0.5,
    inside: !!inside,
    entrances: entrances.length > 0,
    doors: doors.length > 0,
    assets: assets.length > 0,
    cameras: cameras.length > 0 && !pendingCam,
    check: false,
  }
  const next = () => setStep(steps[stepIndex + 1][0])
  const back = () => setStep(steps[stepIndex - 1][0])

  // ---- clicking on the drawing ---------------------------------------------------------------------
  function onPick(pt) {
    if (step === 'scale') {
      if (!scale.p1 || (scale.p1 && scale.p2)) setScale({ ...scale, p1: pt, p2: null })
      else setScale({ ...scale, p2: pt })
    } else if (step === 'inside') {
      setInside(pt)
    } else if (step === 'entrances') {
      setEntrances((l) => [...l, { name: `Entrance ${l.length + 1}`, point: pt }])
    } else if (step === 'doors') {
      setDoors((l) => [...l, { name: `Door ${l.length + 1}`, point: pt }])
    } else if (step === 'assets') {
      setAssets((l) => [...l, { name: `Protected spot ${l.length + 1}`, point: pt }])
    } else if (step === 'cameras') {
      if (!pendingCam) {
        setPendingCam(pt)
      } else if (distance(pendingCam, pt) > 4) {
        const used = new Set(cameras.map((c) => c.name))
        let n = cameras.length + 1
        while (used.has(`C${n}`)) n += 1
        setCameras((l) => [...l, { name: `C${n}`, position: pendingCam, aim: pt, fov_deg: indoor ? 110 : 90, range_m: indoor ? 10 : 9, online: true }])
        setPendingCam(null)
      }
    }
  }
  const picking = ['scale', 'inside', 'entrances', 'doors', 'assets', 'cameras'].includes(step)

  const edit = (list, setList, i, patch) => setList(list.map((x, j) => (j === i ? { ...x, ...patch } : x)))
  const remove = (list, setList, i) => setList(list.filter((_, j) => j !== i))

  // ---- the layer drawn on top of the picture -------------------------------------------------------
  function markers(u, hover) {
    const dot = (key, pt, cls, label) => (
      <g key={key} className={`pm ${cls}`}>
        <circle cx={pt[0]} cy={pt[1]} r={6 * u} />
        {label && <text x={pt[0] + 9 * u} y={pt[1] - 8 * u} fontSize={13 * u}>{label}</text>}
      </g>
    )
    const out = []
    if (scale.p1) out.push(dot('s1', scale.p1, 'pm-scale', 'A'))
    if (scale.p2) out.push(dot('s2', scale.p2, 'pm-scale', 'B'))
    if (scale.p1 && (scale.p2 || (step === 'scale' && hover))) {
      const end = scale.p2 || hover
      out.push(<line key="sl" className="pm-line" x1={scale.p1[0]} y1={scale.p1[1]} x2={end[0]} y2={end[1]} strokeWidth={2.5 * u} />)
    }
    if (inside) out.push(dot('in', inside, 'pm-inside', 'inside'))
    entrances.forEach((e, i) => out.push(dot(`e${i}`, e.point, 'pm-entry', e.name)))
    doors.forEach((d, i) => out.push(dot(`d${i}`, d.point, 'pm-entry', d.name)))
    assets.forEach((a, i) => out.push(dot(`a${i}`, a.point, 'pm-asset', a.name)))
    cameras.forEach((c, i) => {
      if (ppm) {
        const h = headingOf(c.position, c.aim)
        const pts = [c.position]
        for (let k = 0; k <= 16; k++) {
          const a = ((h - c.fov_deg / 2 + (c.fov_deg * k) / 16) * Math.PI) / 180
          pts.push([c.position[0] + c.range_m * ppm * Math.cos(a), c.position[1] - c.range_m * ppm * Math.sin(a)])
        }
        out.push(<polygon key={`w${i}`} className="pm-wedge" points={pts.map((p) => p.join(',')).join(' ')} />)
      }
      out.push(dot(`c${i}`, c.position, 'pm-camera', c.name))
    })
    if (pendingCam) {
      out.push(dot('pc', pendingCam, 'pm-camera', 'new camera'))
      if (hover) out.push(<line key="pl" className="pm-line" x1={pendingCam[0]} y1={pendingCam[1]} x2={hover[0]} y2={hover[1]} strokeWidth={2.5 * u} />)
    }
    return out
  }

  // The "check" step draws what the server read, in the drawing's own pixels.
  function overlay(u) {
    if (check.status !== 'ok') return null
    const o = check.data.overlay
    const poly = (ring) => ring.map((p) => p.join(',')).join(' ')
    return (
      <g>
        <polygon className="ov-floor" points={poly(o.boundary)} />
        {o.obstacles.map((r, i) => <polygon key={i} className="ov-wall" points={poly(r)} />)}
        {o.cameras.map((c) => (
          <g key={c.name}>
            <polygon className="pm-wedge" points={poly(c.wedge)} />
            <g className="pm pm-camera"><circle cx={c.position[0]} cy={c.position[1]} r={6 * u} /><text x={c.position[0] + 9 * u} y={c.position[1] - 8 * u} fontSize={13 * u}>{c.name}</text></g>
          </g>
        ))}
        {(o.entrances || []).map((e) => <g key={e.id} className="pm pm-entry"><circle cx={e.point[0]} cy={e.point[1]} r={6 * u} /><text x={e.point[0] + 9 * u} y={e.point[1] - 8 * u} fontSize={13 * u}>{e.id.replace(/_/g, ' ')}</text></g>)}
        {(o.assets || []).map((e) => <g key={e.id} className="pm pm-asset"><circle cx={e.point[0]} cy={e.point[1]} r={6 * u} /><text x={e.point[0] + 9 * u} y={e.point[1] - 8 * u} fontSize={13 * u}>{e.id.replace(/_/g, ' ')}</text></g>)}
        {(o.doors || []).map((e) => <g key={e.id} className="pm pm-entry"><circle cx={e.point[0]} cy={e.point[1]} r={6 * u} /><text x={e.point[0] + 9 * u} y={e.point[1] - 8 * u} fontSize={13 * u}>{e.id.replace(/_/g, ' ')}</text></g>)}
      </g>
    )
  }

  const recent = recentSites()

  // ======================================================================================== render
  return (
    <Shell
      onHome={onCancel}
      nav={false}
      top={
        <>
          <h1 className="topbar-title">Add your own plan</h1>
          <button className="button" onClick={onCancel}>Cancel</button>
        </>
      }
    >
      <div className="wizard-card wizard">
      <ol className="stepper" aria-label="Steps">
        {steps.map(([id, label], i) => (
          <li key={id} className={id === step ? 'is-current' : i < stepIndex ? 'is-done' : undefined} aria-current={id === step ? 'step' : undefined}>
            {label}
          </li>
        ))}
      </ol>

      <div className="wizard-body">
        <div className="wizard-canvas">
          {image ? (
            <PlanCanvas src={image.url} width={image.width} height={image.height} picking={picking} onPick={onPick}>
              {(u, hover) => (step === 'check' ? overlay(u) : markers(u, hover))}
            </PlanCanvas>
          ) : (
            <div className="dropzone" role="group" aria-label="Choose a plan">
              <p>Your plan will appear here.</p>
            </div>
          )}
        </div>

        <aside className="wizard-panel">
          {/* ------------------------------------------------------------------- 1 plan */}
          {step === 'plan' && (
            <>
              <h2 className="section-heading">What are we looking at?</h2>
              <fieldset className="kind">
                <legend className="sr-only">Kind of plan</legend>
                <label className={indoor ? 'kind-option is-active' : 'kind-option'}>
                  <input type="radio" name="kind" checked={indoor} onChange={() => setKind('indoor')} />
                  <span><strong>Inside a building</strong><br /><span className="muted">A floor plan: a lab, shop, office or warehouse.</span></span>
                </label>
                <label className={!indoor ? 'kind-option is-active' : 'kind-option'}>
                  <input type="radio" name="kind" checked={!indoor} onChange={() => setKind('outdoor')} />
                  <span><strong>Outdoors</strong><br /><span className="muted">A property with a building and grounds.</span></span>
                </label>
              </fieldset>

              <label className="field-label" htmlFor="site-name">Name</label>
              <input id="site-name" className="text-input" value={name} maxLength={60} onChange={(e) => setName(e.target.value)} placeholder={indoor ? 'Chemistry lab, level 2' : 'Smith street house'} />

              <label className="field-label" htmlFor="plan-file">Plan (PNG, JPEG or PDF)</label>
              <input
                id="plan-file"
                className="file-input"
                type="file"
                accept="image/png,image/jpeg,image/webp,image/gif,image/bmp,application/pdf,.pdf"
                onChange={(e) => e.target.files?.[0] && useFile(e.target.files[0])}
              />
              {pdf && pdf.pages > 1 && (
                <div className="pdf-pages">
                  <label htmlFor="pdf-page" className="field-label">PDF page</label>
                  <select id="pdf-page" value={pdf.page} onChange={(e) => useFile(pdf.file, Number(e.target.value))}>
                    {Array.from({ length: pdf.pages }, (_, i) => <option key={i} value={i}>Page {i + 1}</option>)}
                  </select>
                </div>
              )}
              {loading && <p className="muted" role="status">Reading the file…</p>}
              {loadError && <p className="problem" role="alert">{loadError}</p>}

              <p className="muted small">
                Works best with dark walls on a light background. Door swings and dimension lines are fine. If your
                drawing is a CAD or Revit file, export it as a PDF or PNG first. The picture is read in your browser
                and by the server once; only the outline is kept.
              </p>

              {recent.length > 0 && (
                <>
                  <h3 className="floor-sub">Your uploaded sites</h3>
                  <ul className="recent">
                    {recent.map((s) => (
                      <li key={s.id}><button className="button button-quiet" onClick={() => onOpen(s.id)}>{s.name || 'Untitled'}</button></li>
                    ))}
                  </ul>
                </>
              )}
              <p className="muted small">No plan to hand? <button className="link-button" onClick={useSample} disabled={loading}>Try a sample lab</button>.</p>
            </>
          )}

          {/* ------------------------------------------------------------------- 2 scale */}
          {step === 'scale' && (
            <>
              <h2 className="section-heading">How big is it?</h2>
              <p>Click two points on the drawing whose real distance you know: both ends of a wall, or the width of a door (usually 0.9 m).</p>
              <p className="muted small">
                {!scale.p1 ? 'Click the first point.' : !scale.p2 ? 'Now click the second point.' : 'Click again to start over.'}
              </p>
              <label className="field-label" htmlFor="metres">Distance between the two points, in metres</label>
              <input id="metres" className="text-input" inputMode="decimal" value={scale.metres} onChange={(e) => setScale({ ...scale, metres: e.target.value })} placeholder="20" />
              {scale.p1 && scale.p2 && ppm && ppm <= 0.5 && <p className="problem" role="alert">That scale looks too small. Check the two points and the distance.</p>}
              {ppm && ppm > 0.5 && <p className="muted small">{Math.round(ppm * 10) / 10} pixels per metre. The longer the distance you pick, the more exact the plan.</p>}
            </>
          )}

          {/* ------------------------------------------------------------------- 3 inside */}
          {step === 'inside' && (
            <>
              <h2 className="section-heading">{indoor ? 'Where is the floor?' : 'Where is the ground?'}</h2>
              <p>
                {indoor
                  ? 'Click once on open floor inside the outer walls, such as the middle of a corridor. We start from there and find the outline.'
                  : 'Click once on the ground inside the property boundary, outside the house, such as on the lawn.'}
              </p>
              {inside && <p className="muted small">Marked. Click again to move it.</p>}
            </>
          )}

          {/* ------------------------------------------------------------------- 4 entrances / doors */}
          {step === 'entrances' && (
            <PointList
              title="Where do people come in?"
              help="Click each doorway from outside into this floor, in the gap of the outer wall. Intruders are started here."
              items={entrances}
              onRename={(i, v) => edit(entrances, setEntrances, i, { name: v })}
              onRemove={(i) => remove(entrances, setEntrances, i)}
              empty="Click on the drawing to add the first entrance."
            />
          )}
          {step === 'doors' && (
            <PointList
              title="Where are the doors?"
              help="Click each door on the outline of the house. We move the point onto the wall for you."
              items={doors}
              onRename={(i, v) => edit(doors, setDoors, i, { name: v })}
              onRemove={(i) => remove(doors, setDoors, i)}
              empty="Click on the drawing to add the first door."
            />
          )}

          {/* ------------------------------------------------------------------- 5 protected */}
          {step === 'assets' && (
            <PointList
              title="What must stay safe?"
              help="Click inside each room or spot you want protected: a server room, a safe, a freezer, a stock room. We check whether anyone can reach it unseen."
              items={assets}
              onRename={(i, v) => edit(assets, setAssets, i, { name: v })}
              onRemove={(i) => remove(assets, setAssets, i)}
              empty="Click on the drawing to add the first protected spot."
            />
          )}

          {/* ------------------------------------------------------------------- 6 cameras */}
          {step === 'cameras' && (
            <>
              <h2 className="section-heading">Where are the cameras?</h2>
              <p>Click where a camera is fixed, then click a point it faces. Do this for each camera.</p>
              <p className="muted small" role="status">
                {pendingCam ? 'Now click the spot this camera points at.' : cameras.length === 0 ? 'Click the first camera’s position.' : 'Click to add another camera.'}
              </p>
              {pendingCam && <button className="button button-quiet" onClick={() => setPendingCam(null)}>Cancel this camera</button>}
              <ul className="cam-edit">
                {cameras.map((c, i) => (
                  <li key={i}>
                    <div className="cam-edit-row">
                      <label className="sr-only" htmlFor={`cn${i}`}>Camera name</label>
                      <input id={`cn${i}`} className="text-input cam-name" value={c.name} maxLength={12} onChange={(e) => edit(cameras, setCameras, i, { name: e.target.value })} />
                      <button className="button button-quiet" onClick={() => remove(cameras, setCameras, i)}>Remove</button>
                    </div>
                    <div className="cam-edit-row">
                      <label htmlFor={`cf${i}`} className="small">Field of view</label>
                      <select id={`cf${i}`} value={c.fov_deg} onChange={(e) => edit(cameras, setCameras, i, { fov_deg: Number(e.target.value) })}>
                        {[...new Set([...FOV_CHOICES, c.fov_deg])].sort((a, b) => a - b).map((f) => <option key={f} value={f}>{f}°</option>)}
                      </select>
                      <label htmlFor={`cr${i}`} className="small">Range</label>
                      <input id={`cr${i}`} className="text-input cam-range" inputMode="decimal" value={c.range_m} onChange={(e) => edit(cameras, setCameras, i, { range_m: Number(e.target.value) || 0 })} />
                      <span className="small muted">m</span>
                    </div>
                  </li>
                ))}
              </ul>
              <p className="muted small">If you are unsure of a camera’s field of view or range, leave the defaults. They match a typical Ring camera.</p>
            </>
          )}

          {/* ------------------------------------------------------------------- 7 check */}
          {step === 'check' && (
            <>
              <h2 className="section-heading">Is this right?</h2>
              {check.status === 'loading' && <p className="muted" role="status">Reading your plan…</p>}
              {check.status === 'error' && (
                <>
                  <p className="problem" role="alert">{check.message}</p>
                  <p className="muted small">Go back and check the points you clicked, or try the options below.</p>
                </>
              )}
              {check.status === 'ok' && (
                <>
                  <p>
                    We found a {indoor ? 'floor' : 'property'} of <strong>{check.data.meta.width_m} × {check.data.meta.height_m} m</strong>
                    {indoor ? <> with <strong>{check.data.meta.area_m2} m²</strong> of floor</> : null}. The shaded area is what we read; the grey shapes are
                    {indoor ? ' walls and furniture' : ' buildings'}.
                  </p>
                  {check.data.warnings.length > 0 && (
                    <ul className="warnings" role="alert">
                      {check.data.warnings.map((w) => <li key={w}>{w}</li>)}
                    </ul>
                  )}
                  {check.data.preview?.watched_frac !== undefined && (
                    <p className="check-preview">
                      With the cameras as you drew them, <strong>{Math.round(check.data.preview.watched_frac * 100)}%</strong> of the open floor is watched
                      {check.data.preview.blind_m2 > 0 ? <> and <strong>{check.data.preview.blind_m2} m²</strong> is blind</> : null}.
                    </p>
                  )}
                  <p className="muted small">If a wall is missing, a doorway is closed, or the shaded area is wrong, adjust below and read again. Otherwise save.</p>
                </>
              )}

              <details className="advanced">
                <summary>If the outline looks wrong</summary>
                <p className="muted small">
                  <strong>Gap in the outline:</strong> raise the gap size (pixels) if walls are drawn with dashes or small breaks.<br />
                  <strong>Doors look closed:</strong> raise the thin line size to remove thin door swing arcs (try 3).
                </p>
                <label className="small" htmlFor="gap">Gap size, pixels</label>
                <input id="gap" className="text-input cam-range" inputMode="numeric" value={advanced.gap_px} placeholder="3" onChange={(e) => setAdvanced({ ...advanced, gap_px: e.target.value.replace(/\D/g, '') })} />
                <label className="small" htmlFor="thin">Thin line size, pixels</label>
                <input id="thin" className="text-input cam-range" inputMode="numeric" value={advanced.thin_px} placeholder="auto" onChange={(e) => setAdvanced({ ...advanced, thin_px: e.target.value.replace(/\D/g, '') })} />
                <button className="button" onClick={() => setNonce((n) => n + 1)}>Read again</button>
              </details>

              {saveError && <p className="problem" role="alert">{saveError}</p>}
              <div className="wizard-nav">
                <button className="button button-quiet" onClick={back}>Back</button>
                <button className="button button-primary" disabled={check.status !== 'ok' || saving} onClick={save}>
                  {saving ? 'Saving…' : 'Save and see results'}
                </button>
              </div>
            </>
          )}

          {step !== 'check' && (
            <div className="wizard-nav">
              {stepIndex > 0 && <button className="button button-quiet" onClick={back}>Back</button>}
              <button className="button button-primary" disabled={!ready[step]} onClick={next}>Next</button>
            </div>
          )}
        </aside>
      </div>
      </div>
    </Shell>
  )
}

/** A named list of clicked points, with rename and remove. Used for entrances, doors and protected spots. */
function PointList({ title, help, items, onRename, onRemove, empty }) {
  return (
    <>
      <h2 className="section-heading">{title}</h2>
      <p>{help}</p>
      {items.length === 0 ? (
        <p className="muted small" role="status">{empty}</p>
      ) : (
        <ul className="point-list">
          {items.map((it, i) => (
            <li key={i}>
              <label className="sr-only" htmlFor={`pl${i}`}>Name</label>
              <input id={`pl${i}`} className="text-input" value={it.name} maxLength={40} onChange={(e) => onRename(i, e.target.value)} />
              <button className="button button-quiet" onClick={() => onRemove(i)}>Remove</button>
            </li>
          ))}
        </ul>
      )}
    </>
  )
}
