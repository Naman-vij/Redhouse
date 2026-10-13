// SiteMap.jsx - the site plan. This is the centre of the page.
//
// It draws, from bottom to top:
//   survey grid -> camera coverage (teal) -> house -> camera view wedges
//   -> intruder routes (red = got in unseen, faint = caught) -> doors -> cameras
//
// COORDINATES: the backend sends metres with y pointing NORTH. SVG has y
// pointing DOWN, so every y goes through Y() below. We use metres as the
// SVG units (viewBox), so a width of 0.1 means 10 centimetres.
//
// NOTE: colours are CSS variables (see styles.css), so the same drawing
// works in light and dark mode without any code here knowing which is on.

import { useMemo, useRef } from 'react'
import { doorName, pct } from '../lib/format'

// Space around the property, in metres, for the scale bar, labels and north arrow.
const PAD = { left: 2.8, right: 2.4, top: 2.2, bottom: 3.6 }

// The drawing's markers (camera dots, labels, doors) are sized in metres for a property about 24 m across.
// A plan much smaller or larger gets them scaled to match, so they never swallow a small lab or vanish on a campus.
const markerScale = (span) => Math.max(0.45, Math.min(3, span / 24))
const niceScaleBar = (span) => (span < 14 ? 2 : span < 40 ? 5 : span < 90 ? 10 : 20)

const rad = (deg) => (deg * Math.PI) / 180

/** The wedge a camera can see, as an SVG points string. */
function wedgePoints(cam, Y) {
  const [cx, cy] = cam.position
  const pts = [[cx, Y(cy)]]
  const steps = 28
  for (let i = 0; i <= steps; i++) {
    const a = rad(cam.heading_deg - cam.fov_deg / 2 + (cam.fov_deg * i) / steps)
    pts.push([cx + cam.range_m * Math.cos(a), Y(cy + cam.range_m * Math.sin(a))])
  }
  return pts.map((p) => p.map((n) => n.toFixed(2)).join(',')).join(' ')
}

/** A small arrow-head showing which way a camera points. */
function pointer(cam, Y) {
  const [cx, cy] = cam.position
  const a = rad(cam.heading_deg)
  const tip = [cx + 1.25 * Math.cos(a), cy + 1.25 * Math.sin(a)]
  const left = [cx + 0.55 * Math.cos(a + 2.2), cy + 0.55 * Math.sin(a + 2.2)]
  const right = [cx + 0.55 * Math.cos(a - 2.2), cy + 0.55 * Math.sin(a - 2.2)]
  return [tip, left, right].map(([x, y]) => `${x.toFixed(2)},${Y(y).toFixed(2)}`).join(' ')
}

const polyPoints = (pts, Y) => pts.map(([x, y]) => `${x},${Y(y)}`).join(' ')
const pathOf = (pts, Y) => pts.map(([x, y], i) => `${i ? 'L' : 'M'}${x},${Y(y)}`).join(' ')

export default function SiteMap({
  scene,
  door,
  layers,
  liveNames,
  ghosts, // [{ name, heading_deg }] - where each previewed camera pointed BEFORE the change
  headings, // { name: degrees } - a camera's heading right now, ahead of the server's answer
  selected, // name of the camera being turned, or null
  loading,
  onSelectDoor,
  onSelectCamera,
  onAim, // (name, headingDegrees) - the handle was dragged to point this way
  onNudge, // (name, degrees) - arrow keys
}) {
  const svgRef = useRef(null)
  const dragging = useRef(false)
  const { site, coverage, routes } = scene
  const indoor = site.mode === 'indoor'
  // While a camera is being turned, draw it at its NEW heading straight away.
  // The coverage and routes catch up when the simulator answers.
  const cams = site.cameras.map((c) => (headings && headings[c.name] !== undefined ? { ...c, heading_deg: headings[c.name] } : c))
  const xs = site.boundary.map((p) => p[0])
  const ys = site.boundary.map((p) => p[1])
  const minx = Math.min(...xs), maxx = Math.max(...xs)
  const miny = Math.min(...ys), maxy = Math.max(...ys)
  const Y = (y) => maxy - y
  const k = markerScale(Math.max(maxx - minx, maxy - miny)) * (indoor ? 0.7 : 1) // floor plans are denser: smaller markers
  const bar = niceScaleBar(Math.max(maxx - minx, maxy - miny))
  // scale a marker about its own centre
  const at = (x, y) => `translate(${x} ${Y(y)}) scale(${k}) translate(${-x} ${-Y(y)})`
  const pad = { left: PAD.left * k, right: PAD.right * k, top: PAD.top * k, bottom: PAD.bottom * k }

  const viewBox = [
    minx - pad.left,
    -pad.top,
    maxx - minx + pad.left + pad.right,
    maxy - miny + pad.top + pad.bottom,
  ].join(' ')

  // One small square per grid cell where a camera might see someone.
  // NOTE: memoised, because this is ~1,000 shapes and only changes when
  // the coverage numbers do.
  const coverageCells = useMemo(() => {
    const g = site.grid_m
    const cells = []
    coverage.forEach((row, r) => {
      row.forEach((p, c) => {
        if (p < 0.03) return
        cells.push(
          <rect
            key={`${r}-${c}`}
            x={site.origin[0] + c * g - 0.01}
            y={Y(site.origin[1] + (r + 1) * g) - 0.01}
            width={g + 0.02}
            height={g + 0.02}
            fillOpacity={Math.min(0.85, p) * 0.85}
          />,
        )
      })
    })
    return cells
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [coverage, site.grid_m, site.origin[0], site.origin[1], maxy])

  // Doors: put each label on the side AWAY from the house so it never sits on the building.
  // (Indoors the labels lean away from the middle of the floor instead.)
  const house = indoor ? [] : site.obstacles[0] || []
  const hx = house.length ? house.reduce((s, p) => s + p[0], 0) / house.length : (minx + maxx) / 2
  const hy = house.length ? house.reduce((s, p) => s + p[1], 0) / house.length : (miny + maxy) / 2

  const activeRate = scene.doors.find((d) => d.door === door)?.rate
  const sceneKey = `${door}|${scene.archetype}|${site.cameras.map((c) => `${c.name}${c.online ? '' : 'x'}${c.heading_deg}${c.position[0]}`).join('')}`

  return (
    <svg
      ref={svgRef}
      className="sitemap"
      viewBox={viewBox}
      role="img"
      aria-labelledby="map-title map-desc"
      aria-busy={loading}
    >
      <title id="map-title">
        {indoor ? 'Floor plan with cameras, blind spots and intruder routes' : 'Site plan of the property with cameras and intruder routes'}
      </title>
      <desc id="map-desc">
        {door ? `${doorName(door)}: ` : ''}
        {activeRate !== undefined
          ? `${pct(activeRate)} of 100 simulated intruders ${indoor ? 'reach it' : 'get in'} unseen. `
          : ''}
        {scene.summary}
      </desc>

      <defs>
        <pattern id="survey" width="2" height="2" patternUnits="userSpaceOnUse">
          <path d="M2 0H0V2" className="survey-line" />
        </pattern>
        <pattern id="hatch" width="0.55" height="0.55" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect className="hatch-bg" width="0.55" height="0.55" />
          <line x1="0" y1="0" x2="0" y2="0.55" className="hatch-line" />
        </pattern>
        <pattern id="hatch-off" width="0.5" height="0.5" patternUnits="userSpaceOnUse" patternTransform="rotate(-45)">
          <line x1="0" y1="0" x2="0" y2="0.5" className="hatch-off-line" />
        </pattern>
        <pattern id="blindhatch" width="0.5" height="0.5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
          <rect className="blind-bg" width="0.5" height="0.5" />
          <line x1="0" y1="0" x2="0" y2="0.5" className="blind-line" />
        </pattern>
        <filter id="soft" x="-5%" y="-5%" width="110%" height="110%">
          <feGaussianBlur stdDeviation="0.22" />
        </filter>
        <clipPath id="property">
          <polygon points={polyPoints(site.boundary, Y)} />
        </clipPath>
      </defs>

      {/* survey grid, every 2 m */}
      <rect className="paper" x={minx - pad.left} y={-pad.top} width={maxx - minx + pad.left + pad.right} height={maxy - miny + pad.top + pad.bottom} />
      <rect fill="url(#survey)" x={minx - pad.left} y={-pad.top} width={maxx - minx + pad.left + pad.right} height={maxy - miny + pad.top + pad.bottom} />

      {/* the property */}
      <polygon className="ground" points={polyPoints(site.boundary, Y)} />

      {/* where the cameras can see (teal; paler = less likely to be seen) */}
      {layers.coverage && (
        <g clipPath="url(#property)">
          <g className="coverage" filter={indoor ? undefined : 'url(#soft)'}>{coverageCells}</g>
        </g>
      )}

      {/* indoors: the floor nobody watches */}
      {indoor && layers.blind && scene.floor && (
        <g clipPath="url(#property)" className="blind">
          {scene.floor.blind_runs.map(([r, c0, c1]) => (
            <rect
              key={`${r}-${c0}`}
              x={site.origin[0] + c0 * site.grid_m - 0.01}
              y={Y(site.origin[1] + (r + 1) * site.grid_m) - 0.01}
              width={(c1 - c0) * site.grid_m + 0.02}
              height={site.grid_m + 0.02}
            />
          ))}
        </g>
      )}

      {/* the house */}
      {site.obstacles.map((ob, i) => (
        <polygon key={i} className={indoor ? 'wall' : 'house'} points={polyPoints(ob, Y)} />
      ))}
      {house.length > 0 && (
        <text className="map-label house-label" x={hx} y={Y(hy)} textAnchor="middle" dominantBaseline="middle">
          House
        </text>
      )}

      {/* the property line goes on top of the fills so it stays crisp */}
      <polygon className={indoor ? 'floor-line' : 'property-line'} points={polyPoints(site.boundary, Y)} />

      {/* what each camera can see: the nominal view wedge. Not drawn indoors: walls block the view, so the
          teal coverage is the truth there and a wedge through a wall would mislead. */}
      {!indoor && cams.map((cam) => (
        <polygon
          key={`wedge-${cam.name}`}
          className={!cam.online ? 'wedge wedge-off' : cam.proposed ? 'wedge wedge-new' : 'wedge'}
          points={wedgePoints(cam, Y)}
        />
      ))}
      {!indoor && ghosts.map((g) => {
        const cam = cams.find((c) => c.name === g.name)
        return cam ? (
          <polygon
            key={`ghost-${g.name}`}
            className="wedge wedge-ghost"
            points={wedgePoints({ ...cam, heading_deg: g.heading_deg }, Y)}
          />
        ) : null
      })}

      {/* intruder routes. Keyed so the red routes draw in again whenever the situation changes. */}
      {layers.routes && (
        <g key={sceneKey} className="routes">
          {routes.caught.map((r, i) => (
            <path key={`c${i}`} className="route route-caught" d={pathOf(r, Y)} />
          ))}
          {routes.breached.map((r, i) => (
            <g key={`b${i}`}>
              <path
                className="route route-breach"
                d={pathOf(r, Y)}
                pathLength="1"
                style={{ animationDelay: `${i * 0.045}s` }}
              />
              <circle className="route-start" cx={r[0][0]} cy={Y(r[0][1])} r="0.26" />
            </g>
          ))}
        </g>
      )}

      {/* doors */}
      {site.entrances.map((e) => {
        const [x, y] = e.point
        // outdoors the label leans away from the house; indoors it leans in toward the middle of the floor, so it stays on the sheet
        const dx = (indoor ? -1 : 1) * (x - hx), dy = (indoor ? -1 : 1) * (y - hy)
        const len = Math.hypot(dx, dy) || 1
        const ux = dx / len, uy = dy / len
        const rate = scene.doors.find((d) => d.door === e.id)?.rate
        const active = e.id === door
        const anchor = ux > 0.4 ? 'start' : ux < -0.4 ? 'end' : 'middle'
        return (
          <g
            key={e.id}
            transform={at(x, y)}
            className={active ? 'door door-active' : 'door'}
            onClick={() => onSelectDoor(e.id)}
            tabIndex={0}
            role="button"
            aria-label={`${doorName(e.id)}: ${rate !== undefined ? pct(rate) : '?'} of 100 ${indoor ? 'reach it unseen' : 'get in'}. Select to trace routes to ${indoor ? 'it' : 'this door'}.`}
            aria-pressed={active}
            onKeyDown={(ev) => {
              if (ev.key === 'Enter' || ev.key === ' ') {
                ev.preventDefault()
                onSelectDoor(e.id)
              }
            }}
          >
            {active && <circle className="door-ring" cx={x} cy={Y(y)} r="0.95" />}
            <circle className="door-dot" cx={x} cy={Y(y)} r="0.42" />
            <text
              className="map-label door-label"
              x={x + ux * 1.7}
              y={Y(y + uy * 1.7)}
              textAnchor={anchor}
              dominantBaseline="middle"
            >
              {doorName(e.id)}
            </text>
          </g>
        )
      })}

      {/* indoors: where intruders come in from outside */}
      {indoor && (site.entries || []).map((e) => (
        <g key={`entry-${e.id}`} className="entry" transform={at(e.point[0], e.point[1])}>
          <rect className="entry-mark" x={e.point[0] - 0.4} y={Y(e.point[1]) - 0.4} width="0.8" height="0.8" transform={`rotate(45 ${e.point[0]} ${Y(e.point[1])})`} />
          <text className="map-label entry-label" x={e.point[0]} y={Y(e.point[1]) + (e.point[1] < (miny + maxy) / 2 ? 1.5 : -1.2)} textAnchor="middle" dominantBaseline="middle">
            {doorName(e.id)}
          </text>
        </g>
      ))}

      {/* cameras: a dot, a pointer showing the direction, a name. A second ring marks a camera that is live from Ring. */}
      {cams.map((cam) => {
        const [x, y] = cam.position
        const live = liveNames.includes(cam.name)
        const canTurn = !cam.proposed
        const select = () => onSelectCamera(cam.name)
        return (
          <g
            key={cam.name}
            transform={at(x, y)}
            className={[cam.proposed ? 'camera camera-new' : cam.online ? 'camera' : 'camera camera-off', canTurn ? 'camera-turnable' : '', cam.name === selected ? 'camera-selected' : ''].join(' ')}
            {...(canTurn
              ? {
                  onClick: select,
                  tabIndex: 0,
                  role: 'button',
                  'aria-pressed': cam.name === selected,
                  'aria-label': `Camera ${cam.name}. Select to turn it. When selected, the left and right arrow keys turn it.`,
                  onKeyDown: (ev) => {
                    if (ev.key === 'Enter' || ev.key === ' ') {
                      ev.preventDefault()
                      select()
                    } else if (cam.name === selected && (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight')) {
                      ev.preventDefault()
                      onNudge(cam.name, (ev.key === 'ArrowLeft' ? 1 : -1) * (ev.shiftKey ? 15 : 5))
                    }
                  },
                }
              : {})}
          >
            {canTurn && <circle className="camera-hit" cx={x} cy={Y(y) + 0.7} r="1.6" />}
            {cam.name === selected && <circle className="camera-select-ring" cx={x} cy={Y(y)} r="1.15" />}
            <polygon className="camera-pointer" points={pointer(cam, Y)} />
            {cam.proposed && <circle className="camera-new-ring" cx={x} cy={Y(y)} r="0.95" />}
            {live && <circle className="camera-live" cx={x} cy={Y(y)} r="0.95" />}
            <circle className="camera-dot" cx={x} cy={Y(y)} r="0.5" />
            {!cam.online && (
              <path className="camera-cross" d={`M${x - 0.3},${Y(y) - 0.3}L${x + 0.3},${Y(y) + 0.3}M${x - 0.3},${Y(y) + 0.3}L${x + 0.3},${Y(y) - 0.3}`} />
            )}
            <text className="map-label camera-label" x={x} y={Y(y) + 1.65} textAnchor="middle" dominantBaseline="middle">
              {cam.proposed ? `${cam.name} new` : cam.online ? cam.name : `${cam.name} offline`}
            </text>
          </g>
        )
      })}

      {/* the turning handle: drag it round the camera to point the camera another way */}
      {cams.filter((c) => c.name === selected).map((cam) => {
        const [x, y] = cam.position
        const reach = Math.min(cam.range_m * 0.75, 6.5 * k)
        const a = rad(cam.heading_deg)
        const hx2 = x + reach * Math.cos(a)
        const hy2 = y + reach * Math.sin(a)
        const aimFrom = (ev) => {
          const svg = svgRef.current
          const pt = svg.createSVGPoint()
          pt.x = ev.clientX
          pt.y = ev.clientY
          const p = pt.matrixTransform(svg.getScreenCTM().inverse())
          // SVG y points down, the plan's y points north: flip it back.
          return (Math.atan2(-(p.y - Y(y)), p.x - x) * 180) / Math.PI
        }
        return (
          <g key={`handle-${cam.name}`} className="turn-handle-group">
            <line className="turn-guide" x1={x} y1={Y(y)} x2={hx2} y2={Y(hy2)} />
            <circle
              className="turn-handle-hit"
              cx={hx2}
              cy={Y(hy2)}
              r={1.3 * k}
              onPointerDown={(ev) => {
                dragging.current = true
                ev.currentTarget.setPointerCapture(ev.pointerId)
              }}
              onPointerMove={(ev) => {
                if (dragging.current) onAim(cam.name, aimFrom(ev))
              }}
              onPointerUp={(ev) => {
                dragging.current = false
                ev.currentTarget.releasePointerCapture(ev.pointerId)
              }}
              onPointerCancel={() => {
                dragging.current = false
              }}
            />
            <circle className="turn-handle" cx={hx2} cy={Y(hy2)} r={0.55 * k} pointerEvents="none" />
          </g>
        )
      })}

      {/* scale bar */}
      <g className="scale" transform={`translate(${minx}, ${Y(miny) + 1.7 * k}) scale(${k})`}>
        <path d={`M0,0H${bar}M0,-0.25V0.25M${bar / 2},-0.15V0.15M${bar},-0.25V0.25`} />
        <text className="map-label" x={bar + 0.5} y="0" dominantBaseline="middle">{bar} m</text>
      </g>

      {/* north arrow */}
      <g className="north" transform={`translate(${maxx + 1.2 * k}, ${Y(maxy) + 0.4 * k}) scale(${k})`}>
        <path d="M0,1.6V-0.6M0,-0.9L-0.3,-0.2L0.3,-0.2Z" />
        <text className="map-label" x="0" y="2.4" textAnchor="middle">N</text>
      </g>
    </svg>
  )
}
