// Dashboard.jsx - the results page for ONE site (the demo house, or an uploaded plan).
// It owns the state and decides what to ask the backend. App.jsx chooses which site to show.
//
// THE STATE, in one place:
//   archetype      which kind of intruder
//   door           which door we are tracing routes to
//   manualOffline  simulated cameras the user switched off (a what-if)
//   turns          how far each camera is turned from where it is mounted now
//                  { C2: 45 } (positive = counter-clockwise). Set by dragging a
//                  camera, the slider, or a preview button
//   showNew        draw the suggested new cameras
//   source         what set those: null, 'fix', 'best', 'more' or 'custom' (the
//                  user's own turning). Only used for the banner and buttons
//   ring           real Ring status (useRing.js)
//
// WHO DECIDES A CAMERA'S STATE:
//   - a camera linked to a Ring device: Ring decides
//   - any other camera: the user decides, with the switch under the map
//
// Every time one of those changes we ask the backend for a fresh "scene"
// and redraw. Nothing is calculated in the browser; it only displays
// numbers the simulator produced.

import { useEffect, useMemo, useRef, useState } from 'react'
import { deleteSite, getExplain, getFix, getOutage, getPlan, getScene, reportUrl } from './api'
import { doorName, fixAngle, turnText, wrap180 } from './lib/format'
import { useDebounced } from './lib/useDebounced'
import { useRing } from './lib/useRing'
import SiteMap from './components/SiteMap'
import FixCard from './components/FixCard'
import OutageTable from './components/OutageTable'
import RingPanel from './components/RingPanel'
import BestSetup from './components/BestSetup'
import PlainWords from './components/PlainWords'
import FloorCard from './components/FloorCard'
import Shell, { NAV } from './components/Shell'
import Icon from './components/Icon'
import CoverageCard from './components/CoverageCard'
import Recommendations from './components/Recommendations'
import CameraCards from './components/CameraCards'
import ResultsBar from './components/ResultsBar'
import ReportCard from './components/ReportCard'
import { forgetSite, recentSites } from './lib/sites'

export default function Dashboard({ site, onUpload, onHome, onOpen }) {
  const [archetype, setArchetype] = useState('opportunist')
  const [door, setDoor] = useState(null) // null until the first answer picks the most exposed door
  const [manualOffline, setManualOffline] = useState([])
  const [turns, setTurns] = useState({})
  const [showNew, setShowNew] = useState(false)
  const [source, setSource] = useState(null)
  const [selected, setSelected] = useState(null) // camera being turned
  const [view, setView] = useState('both') // what the map shows: 'coverage', 'intruders' or 'both'
  const layers = { coverage: view !== 'intruders', routes: view !== 'coverage', blind: view !== 'intruders' }
  const [current, setCurrent] = useState('plan') // the sidebar item for the section on screen
  const [ringOpen, setRingOpen] = useState(false)

  const [scene, setScene] = useState({ data: null, loading: true, error: null, rotate: null })
  const [fix, setFix] = useState({ status: 'loading' })
  const [outage, setOutage] = useState(null)
  const [plan, setPlan] = useState({ status: 'loading' })

  const ring = useRing(archetype, site)

  // ---- which cameras are offline right now -------------------------------
  const offline = useMemo(() => {
    const off = new Set(manualOffline.filter((n) => !ring.liveNames.includes(n)))
    ring.cameras.filter((c) => c.source === 'ring' && !c.online).forEach((c) => off.add(c.name))
    return [...off].sort()
  }, [manualOffline, ring.cameras, ring.liveNames])

  // ---- turning cameras and previewing plans --------------------------------
  const fixData = fix.status === 'ok' ? fix.data.fix : null
  const planData = plan.status === 'ok' ? plan.data : null

  // The backend wants "C2:45,C3:-75". The turns settle for a moment before we
  // ask, so dragging doesn't send a request for every pixel.
  const turnsKey = (t) =>
    Object.entries(t)
      .filter(([, a]) => a !== 0)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([n, a]) => `${n}:${a}`)
      .join(',')
  const rotateParam = useDebounced(turnsKey(turns), 250) || null
  const addParam = showNew && planData ? planData.more_cameras.add_param || null : null

  const sourceRef = useRef(source)
  sourceRef.current = source

  function resetAll() {
    setTurns({})
    setShowNew(false)
    setSource(null)
  }
  // Used when something the preview depended on changes (another door, another intruder type).
  function resetIf(...kinds) {
    if (kinds.includes(sourceRef.current)) resetAll()
  }

  // A preview button: set the turns (and new cameras) the plan describes, or clear them if already on.
  function applyPreview(which) {
    if (source === which) return resetAll()
    if (which === 'fix' && fixData?.change_type === 'rotate') {
      setTurns({ [fixData.camera]: fixAngle(fixData) })
      setShowNew(false)
    } else if ((which === 'best' || which === 'more') && planData) {
      setTurns(Object.fromEntries(planData.best_existing.changes.map((c) => [c.camera, c.angle])))
      setShowNew(which === 'more')
    } else {
      return
    }
    setSource(which)
  }

  // The user turned a camera by hand.
  function turnCamera(name, angle) {
    const next = { ...turns, [name]: Math.max(-180, Math.min(180, Math.round(angle))) }
    if (next[name] === 0) delete next[name]
    setTurns(next)
    setSource(Object.keys(next).length > 0 || showNew ? 'custom' : null)
  }

  // ---- ask for the scene ---------------------------------------------------
  useEffect(() => {
    const ctrl = new AbortController()
    setScene((s) => ({ ...s, loading: true }))
    getScene({ site, door, archetype, offline, rotate: rotateParam, add: addParam }, ctrl.signal)
      .then((data) => setScene({ data, loading: false, error: null, rotate: rotateParam }))
      .catch((err) => {
        if (err.name !== 'AbortError') setScene((s) => ({ ...s, loading: false, error: err.message }))
      })
    return () => ctrl.abort()
    // offline is rebuilt each render, so depend on its text instead
  }, [site, door, archetype, offline.join(','), rotateParam, addParam]) // eslint-disable-line react-hooks/exhaustive-deps

  // Once the first answer has chosen the most exposed door, keep it fixed so the page doesn't jump around.
  useEffect(() => {
    if (door === null && scene.data) setDoor(scene.data.door)
  }, [door, scene.data])

  const activeDoor = door ?? scene.data?.door

  // ---- the recommended fix (needs a door) ----------------------------------
  // Indoors there is no "fix for a door"; the best setup below answers that question for the whole floor.
  const indoor = scene.data?.site.mode === 'indoor'
  useEffect(() => {
    if (!activeDoor || indoor) return undefined
    const ctrl = new AbortController()
    setFix({ status: 'loading' })
    resetIf('fix')
    getFix(activeDoor, archetype, site, ctrl.signal)
      .then((data) => setFix({ status: 'ok', data }))
      .catch((err) => {
        if (err.name !== 'AbortError') setFix({ status: 'error', message: err.message })
      })
    return () => ctrl.abort()
  }, [site, activeDoor, archetype, indoor])

  // ---- the "if a camera fails" table ----------------------------------------
  useEffect(() => {
    const ctrl = new AbortController()
    getOutage(archetype, site, ctrl.signal)
      .then(setOutage)
      .catch(() => setOutage(null)) // the table is extra; the page works without it
    return () => ctrl.abort()
  }, [site, archetype])

  // ---- the best setup (slow the first time) ----------------------------------
  useEffect(() => {
    const ctrl = new AbortController()
    setPlan({ status: 'loading' })
    resetIf('best', 'more') // another intruder type has another best plan, so showing this one would mislead
    getPlan(archetype, site, ctrl.signal)
      .then((data) => setPlan({ status: 'ok', data }))
      .catch((err) => {
        if (err.name !== 'AbortError') setPlan({ status: 'error', message: err.message })
      })
    return () => ctrl.abort()
  }, [site, archetype])

  // ---- the plain-words summary (asked only after the plan is ready, so the plan is already cached) ----
  const [words, setWords] = useState({ status: 'loading' })
  useEffect(() => {
    if (plan.status !== 'ok') return undefined
    const ctrl = new AbortController()
    setWords({ status: 'loading' })
    getExplain(archetype, site, ctrl.signal)
      .then((data) => setWords({ status: 'ok', data }))
      .catch((err) => {
        if (err.name !== 'AbortError') setWords({ status: 'error' }) // the card is extra; the page works without it
      })
    return () => ctrl.abort()
  }, [plan.status, site, archetype])

  // ---- helpers for the children ----------------------------------------------
  const toggleOffline = (name) =>
    setManualOffline((list) => (list.includes(name) ? list.filter((n) => n !== name) : [...list, name]))

  const data = scene.data
  const live = useMemo(() => {
    const out = {}
    ring.cameras
      .filter((c) => c.source === 'ring')
      .forEach((c) => {
        out[c.name] = { deviceName: c.ring_device_name, reportedAt: c.reported_at }
      })
    return out
  }, [ring.cameras])

  // Suggested (not real) cameras are drawn on the map but are not part of "your cameras".
  const realCameras = data ? data.site.cameras.filter((c) => !c.proposed) : []
  const suggested = data ? data.site.cameras.filter((c) => c.proposed) : []

  const activeRate = data?.doors.find((d) => d.door === activeDoor)?.rate ?? 0

  // What the server's answer already includes, e.g. {C2: 45}. The camera headings it sent back
  // include these turns, so subtracting them gives each camera's mounted heading.
  const applied = useMemo(
    () => Object.fromEntries((scene.rotate || '').split(',').filter(Boolean).map((p) => [p.split(':')[0], Number(p.split(':')[1])])),
    [scene.rotate],
  )
  const mounted = (cam) => cam.heading_deg - (applied[cam.name] ?? 0)

  // Headings to draw right now: the mounted heading plus the turn the user has set, which can be
  // ahead of the server's answer while the numbers are still being worked out.
  const headings = Object.fromEntries(realCameras.map((c) => [c.name, (mounted(c) + (turns[c.name] ?? 0) + 360) % 360]))

  // Where each turned camera pointed BEFORE any turn, drawn as a dotted ghost wedge.
  const ghosts = realCameras
    .filter((c) => (turns[c.name] ?? 0) !== 0)
    .map((c) => ({ name: c.name, heading_deg: mounted(c) }))

  const pendingNumbers = turnsKey(turns) !== (scene.rotate ?? '') || scene.loading

  const selectedCam = realCameras.find((c) => c.name === selected) ?? null
  // What the recommendation would do for the selected camera, if it has one.
  const suggestion = selectedCam
    ? (planData?.best_existing.changes.find((c) => c.camera === selected)?.angle ??
        (fixData?.change_type === 'rotate' && fixData.camera === selected ? fixAngle(fixData) : null))
    : null

  const SOURCE_NAMES = {
    fix: 'the smallest fix',
    best: 'your cameras turned to their best angles',
    more: 'your cameras turned, plus the suggested new cameras',
    custom: 'your own changes',
  }
  const changed = Object.keys(turns).length > 0 || showNew

  const liveCount = ring.liveNames.length

  // The title block: what this sheet shows.
  const siteTitle = useMemo(() => {
    if (!data) return ''
    const xs = data.site.boundary.map((p) => p[0])
    const ys = data.site.boundary.map((p) => p[1])
    const size = `${Math.round(Math.max(...xs) - Math.min(...xs))} × ${Math.round(Math.max(...ys) - Math.min(...ys))} m`
    if (site === null) return `Demo house, ${size} property`
    return `${data.site.name || 'Uploaded plan'}, ${size}${indoor && data.floor ? `, ${Math.round(data.floor.open_floor_m2)} m² open floor` : ''}`
  }, [data, site, indoor])

  async function forget() {
    if (!window.confirm('Delete this site from the server? The plan image was never kept; this removes the outline and cameras.')) return
    try {
      await deleteSite(site)
    } catch (err) {
      if (err.status !== 404) return window.alert(err.message)
    }
    forgetSite(site)
    onHome()
  }

  // ---- the sidebar's highlight follows the section you are looking at ----------
  const hasData = Boolean(data)
  useEffect(() => {
    if (!hasData || typeof IntersectionObserver === 'undefined') return undefined
    const seen = new Map()
    const io = new IntersectionObserver(
      (entries) => {
        entries.forEach((e) => seen.set(e.target.id, e.isIntersecting))
        const first = NAV.find((n) => seen.get(n.id))
        if (first) setCurrent(first.id)
      },
      { rootMargin: '-10% 0px -55% 0px' },
    )
    NAV.forEach((n) => {
      const el = document.getElementById(n.id)
      if (el) io.observe(el)
    })
    return () => io.disconnect()
  }, [hasData, indoor])

  // "Show on map" on a camera card: select it, and bring the map into view only if it is off screen.
  function pickCamera(name) {
    setSelected((cur) => (cur === name ? null : name))
    document.getElementById('plan')?.scrollIntoView({ block: 'nearest' })
  }

  // ---- the site picker: the demo house and the sites this browser has made --------
  const known = recentSites()
  const options = [
    { id: '', label: site === null && siteTitle ? siteTitle : 'Demo house' },
    ...known.filter((k) => k.id !== site).map((k) => ({ id: k.id, label: k.name || 'Uploaded plan' })),
  ]
  if (site !== null && !known.some((k) => k.id === site)) options.push({ id: site, label: siteTitle || 'Uploaded plan' })
  if (site !== null) {
    const here = options.find((o) => o.id === site)
    if (here && siteTitle) here.label = siteTitle
  }
  if (site === null && options[0]) options[0].label = siteTitle || 'Demo house'

  const top = (
    <>
      <label className="site-picker">
        <Icon name="search" size={18} />
        <span className="sr-only">Site to show</span>
        <select value={site ?? ''} onChange={(e) => onOpen(e.target.value)}>
          {options.map((o) => (
            <option key={o.id || 'demo'} value={o.id}>{o.label}</option>
          ))}
        </select>
        <Icon name="chevron" size={16} />
      </label>
      <button className="button" onClick={onUpload}>
        <Icon name="upload" size={17} /> Upload a plan
      </button>
      <button
        className={`button button-dark ring-button ${ring.phase === 'watching' ? 'is-live' : ''}`}
        aria-expanded={ringOpen}
        aria-controls="ring-panel"
        onClick={() => setRingOpen((o) => !o)}
      >
        <Icon name="ring" size={17} />
        {ring.phase === 'watching'
          ? `Ring connected: ${liveCount} live ${liveCount === 1 ? 'camera' : 'cameras'}`
          : 'Connect Ring'}
      </button>
    </>
  )

  const details = data ? (
    <div className="site-details">
      <h2>Site details</h2>
      <dl>
        <div><dt>Site</dt><dd>{siteTitle}</dd></div>
        <div>
          <dt>Cameras</dt>
          <dd>{realCameras.length} total, {liveCount} live from Ring{offline.length > 0 ? `, ${offline.length} offline` : ''}</dd>
        </div>
        <div><dt>{indoor ? 'Routes to' : 'Tracking'}</dt><dd>{activeDoor ? doorName(activeDoor) : ''}</dd></div>
        <div><dt>Simulation</dt><dd>{data.starts_tested} simulated intruders</dd></div>
      </dl>
      {site !== null && <button className="button" onClick={forget}>Delete this site</button>}
    </div>
  ) : null

  return (
    <Shell onHome={onHome} nav details={details} current={current} top={top}>
      {ringOpen && (
        <div id="ring-panel">
          <RingPanel
            ring={ring}
            cameraNames={realCameras.map((c) => c.name)}
            onClose={() => setRingOpen(false)}
          />
        </div>
      )}

      {/* ----------------------------------------------------------- error / loading */}
      {scene.error && !data && (
        <div className="problem problem-block" role="alert">
          <strong>The map could not load.</strong>
          <p>{scene.error}</p>
          {site && <button className="button" onClick={onHome}>Open the demo house</button>}
        </div>
      )}
      {scene.error && data && <p className="problem" role="alert">{scene.error}</p>}
      {!data && !scene.error && <p className="muted loading">Reading the site…</p>}

      {data && (
        <>
          <div className="page">
            <div className="row-top">
              {/* ------------------------------------------------------------ the plan */}
              <section className="card plan-card" id="plan" aria-labelledby="plan-title">
                <div className="plan-head">
                  <div>
                    <h2 id="plan-title">{indoor ? 'Floor plan' : 'Site plan'}</h2>
                    <p>Click a camera, then drag its handle or use its slider. The numbers update as you turn it.</p>
                  </div>
                  <div className="plan-tools">
                    <span className="seg-label" id="view-label">View</span>
                    <div className="seg" role="group" aria-labelledby="view-label">
                      {[['coverage', 'Coverage'], ['intruders', 'Intruders'], ['both', 'Both']].map(([id, label]) => (
                        <button key={id} aria-pressed={view === id} onClick={() => setView(id)}>{label}</button>
                      ))}
                    </div>
                  </div>
                </div>

                {changed && source && (
                  <div className="preview-banner" role="status">
                    <span>
                      Showing {SOURCE_NAMES[source]}.{' '}
                      {Object.entries(turns).filter(([, a]) => a !== 0).map(([n, a]) => `${n} ${turnText(a)}`).join(', ')}
                      {Object.keys(turns).length > 0 ? '. ' : ''}The numbers on this page show that layout.
                    </span>
                    <button className="button button-small" onClick={resetAll}>Back to current cameras</button>
                  </div>
                )}

                <figure className={scene.loading || pendingNumbers ? 'sheet is-loading' : 'sheet'}>
                  <div className="sheet-scroll">
                    <SiteMap
                      scene={data}
                      door={activeDoor}
                      layers={layers}
                      liveNames={ring.liveNames}
                      ghosts={ghosts}
                      headings={headings}
                      selected={selected}
                      loading={scene.loading}
                      onSelectDoor={setDoor}
                      onSelectCamera={(name) => setSelected((cur) => (cur === name ? null : name))}
                      onAim={(name, worldHeading) => {
                        // the handle points at worldHeading; the turn is how far that is from the mounted heading
                        const cam = realCameras.find((c) => c.name === name)
                        if (cam) turnCamera(name, Math.round(wrap180(worldHeading - mounted(cam)) / 5) * 5)
                      }}
                      onNudge={(name, delta) => turnCamera(name, (turns[name] ?? 0) + delta)}
                    />
                  </div>
                  {/* the drawing's title block: facts about THIS sheet */}
                  <figcaption className="title-block">
                    <dl>
                      <div><dt>{indoor ? 'Floor' : 'Site'}</dt><dd>{siteTitle}</dd></div>
                      <div>
                        <dt>Cameras</dt>
                        <dd>
                          {realCameras.length} total, {liveCount} live from Ring
                          {offline.length > 0 ? `, ${offline.length} offline` : ''}
                          {suggested.length > 0 ? `, ${suggested.length} suggested` : ''}
                        </dd>
                      </div>
                      <div><dt>{indoor ? 'Routes to' : 'Tracing'}</dt><dd>{activeDoor ? doorName(activeDoor) : ''}</dd></div>
                      <div><dt>Intruders</dt><dd>{data.starts_tested} simulated</dd></div>
                    </dl>
                  </figcaption>
                </figure>

                <ul className="legend" aria-label="Map key">
                  <li><span className="key key-coverage" /> Seen by a camera (darker is more likely)</li>
                  {indoor && <li><span className="key key-blind" /> Blind spot: under a 1 in 4 chance of being seen</li>}
                  <li><span className="key key-breach" /> Route that {indoor ? 'reached a protected spot' : 'got in'} unseen</li>
                  <li><span className="key key-caught" /> Route that was caught</li>
                  <li><span className="key key-live" /> Camera live from Ring</li>
                  {suggested.length > 0 && <li><span className="key key-new" /> Suggested new camera</li>}
                </ul>
                {!selectedCam && (
                  <p className="muted turn-hint">Pick a camera on the map, or use a camera card below.</p>
                )}
              </section>

              {/* --------------------------------------------------------- right: findings */}
              <aside className="side-stack" aria-busy={scene.loading}>
                <CoverageCard
                  doors={data.doors}
                  active={activeDoor}
                  onSelect={setDoor}
                  rate={activeRate}
                  indoor={indoor}
                  floor={indoor ? data.floor : null}
                  archetype={archetype}
                  summary={indoor ? '' : data.summary}
                  pending={pendingNumbers}
                />
                <Recommendations
                  plan={plan}
                  cameras={realCameras}
                  headings={headings}
                  doors={data.doors}
                  indoor={indoor}
                  floor={indoor ? data.floor : null}
                  preview={source}
                  onPreview={applyPreview}
                />
              </aside>
            </div>

            {/* ------------------------------------------------------------ the cameras */}
            <CameraCards
              cameras={realCameras}
              live={live}
              turns={turns}
              headings={headings}
              selected={selected}
              doors={data.doors}
              floor={indoor ? data.floor : null}
              pending={pendingNumbers}
              plan={plan}
              preview={source}
              onSelect={pickCamera}
              onTurn={turnCamera}
              onToggle={toggleOffline}
              onPreview={applyPreview}
            />

            <div className="row-two">
              <PlainWords state={words} />
              {indoor ? (
                <FloorCard floor={data.floor} summary={data.summary} changed={changed} />
              ) : (
                <FixCard
                  door={activeDoor}
                  state={fix}
                  previewing={source === 'fix'}
                  onTogglePreview={() => applyPreview('fix')}
                  anyOffline={offline.length > 0}
                />
              )}
            </div>

            <BestSetup plan={plan} preview={source} onPreview={applyPreview} anyOffline={offline.length > 0} />

            <div id="analytics">
              <OutageTable data={outage} offline={offline} live={ring.liveNames} onShow={toggleOffline} />
            </div>

            <ReportCard reportHref={plan.status === 'ok' ? reportUrl(archetype, site) : null} archetype={archetype} site={site} />

            <footer className="colophon">
              <p>
                {site === null
                  ? 'This is a simulation of a demo house. '
                  : `This is a simulation of ${indoor ? 'the floor' : 'the property'} you uploaded, read from your drawing. `}
                The intruders are simulated with fixed random seeds, so the same question always gives the same
                answer. Only cameras you link to a Ring device use real Ring data, and then only for their online
                or offline status.
                {indoor &&
                  ' What a camera sees is worked out from the camera’s position, direction, field of view and range, with walls and furniture blocking the view. Furniture drawn as a closed shape counts as solid.'}
              </p>
            </footer>
          </div>

          <ResultsBar
            archetype={archetype}
            onArchetype={setArchetype}
            door={activeDoor}
            rate={activeRate}
            indoor={indoor}
            cameras={realCameras.length}
            starts={data.starts_tested}
            changed={changed}
            onReset={resetAll}
            reportHref={plan.status === 'ok' ? reportUrl(archetype, site) : null}
          />
        </>
      )}
    </Shell>
  )
}
