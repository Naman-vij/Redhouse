// PlanCanvas.jsx - the uploaded drawing with a layer on top you can click.
//
// Everything is in the DRAWING'S OWN PIXELS (the picture we upload is the picture you click on, so
// what you click is exactly what the server reads). `children` is a function that receives `unit`,
// a size in those pixels that looks right on this drawing, so markers are the same on-screen size
// for a small sketch and a big scan.

import { useRef, useState } from 'react'

export default function PlanCanvas({ src, width, height, picking, onPick, children, label }) {
  const svgRef = useRef(null)
  const [zoom, setZoom] = useState(1)
  const [hover, setHover] = useState(null)
  const unit = Math.max(width, height) / 800

  const toPoint = (ev) => {
    const svg = svgRef.current
    const pt = svg.createSVGPoint()
    pt.x = ev.clientX
    pt.y = ev.clientY
    const p = pt.matrixTransform(svg.getScreenCTM().inverse())
    return [Math.max(0, Math.min(width, Math.round(p.x * 10) / 10)), Math.max(0, Math.min(height, Math.round(p.y * 10) / 10))]
  }

  return (
    <div className="canvas">
      <div className="canvas-tools">
        <span className="muted small">{picking ? 'Click on the drawing.' : 'Drawing'}</span>
        <div className="canvas-zoom" role="group" aria-label="Zoom">
          <button className="button button-quiet" onClick={() => setZoom((z) => Math.max(1, z - 0.5))} disabled={zoom <= 1}>
            Zoom out
          </button>
          <span className="muted small" aria-live="polite">{Math.round(zoom * 100)}%</span>
          <button className="button button-quiet" onClick={() => setZoom((z) => Math.min(4, z + 0.5))} disabled={zoom >= 4}>
            Zoom in
          </button>
        </div>
      </div>
      <div className="canvas-scroll">
        <svg
          ref={svgRef}
          className={picking ? 'canvas-svg is-picking' : 'canvas-svg'}
          viewBox={`0 0 ${width} ${height}`}
          style={{ width: `${zoom * 100}%` }}
          role="img"
          aria-label={label || 'The plan you uploaded'}
          onClick={(ev) => picking && onPick(toPoint(ev))}
          onPointerMove={(ev) => picking && setHover(toPoint(ev))}
          onPointerLeave={() => setHover(null)}
        >
          <image href={src} x="0" y="0" width={width} height={height} />
          {typeof children === 'function' ? children(unit, hover) : children}
        </svg>
      </div>
    </div>
  )
}
