// api.js
//
// Every call to the Python backend lives in this one file, so the rest of
// the app never builds a URL by hand.
//
// NOTE: BASE is empty in development, because vite.config.js forwards
// "/api" to the Python server. For AWS, set VITE_API_URL (for example in a
// .env.production file) to the deployed API address.

const BASE = import.meta.env.VITE_API_URL ?? ''

/** An error that carries the HTTP status and a message fit to show a person. */
export class ApiError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

async function request(path, { params = {}, headers = {}, signal, method = 'GET', body } = {}) {
  // Build ?a=1&b=2 - skipping empty values, and repeating a key for lists
  // (the Ring "link" parameter can appear once per camera).
  const qs = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === '') continue
    if (Array.isArray(value)) value.forEach((v) => qs.append(key, v))
    else qs.append(key, value)
  }
  const query = qs.toString()

  let res
  try {
    res = await fetch(`${BASE}${path}${query ? `?${query}` : ''}`, { method, headers, signal, body })
  } catch (err) {
    if (err.name === 'AbortError') throw err // a newer request replaced this one
    throw new ApiError(
      0,
      "Can't reach the RedHouse API. Start it with  uvicorn app.main:app --reload  in the backend folder.",
    )
  }

  if (!res.ok) {
    // The backend explains problems in a "detail" string. Show that.
    let detail = ''
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* the error body was not JSON; fall through to the generic message */
    }
    throw new ApiError(res.status, detail || `The API answered with status ${res.status}.`)
  }
  if (res.status === 204) return null
  return res.json()
}

// Every call takes `site`: the id of an uploaded site, or nothing for the demo house.

/** Everything the map needs for one situation (see backend routes/scene.py). */
export function getScene({ site, door, archetype, offline, rotate, add }, signal) {
  return request('/api/scene', {
    params: { site, door, archetype, offline: offline?.join(','), rotate, add },
    signal,
  })
}

/**
 * The best setup: how to turn the cameras you have, and whether you need
 * more (backend routes/plan.py). The first call takes a few seconds
 * because the backend tries thousands of set-ups; after that it is instant.
 */
export function getPlan(archetype, site, signal) {
  return request('/api/plan', { params: { archetype, site }, signal })
}

/**
 * A short plain-language summary of the best setup (backend routes/explain.py). Written by Amazon Bedrock when
 * it is configured, with every number checked; otherwise the built-in sentences. `source` says which.
 */
export function getExplain(archetype, site, signal) {
  return request('/api/explain', { params: { archetype, site }, signal })
}

/**
 * The address of the PDF report (backend routes/report.py). It is a plain link, not a fetch, so the browser
 * downloads the file itself: <a href={reportUrl(archetype, site)} download>.
 */
export function reportUrl(archetype, site) {
  const qs = new URLSearchParams()
  if (archetype) qs.set('archetype', archetype)
  if (site) qs.set('site', site)
  const query = qs.toString()
  return `${BASE}/api/report${query ? `?${query}` : ''}`
}

/** The recommended fix for a door (backend routes/demo.py). */
/** Email the PDF report to one address. Resolves with nothing; rejects with an ApiError that says what to tell the person. */
export function emailReport(archetype, site, email) {
  return request('/api/report/email', {
    method: 'POST',
    params: { archetype, site },
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email }),
  })
}

export function getFix(door, archetype, site, signal) {
  return request('/api/demo/audit', { params: { door, archetype, site }, signal })
}

/** "If this camera failed" for every camera (backend routes/scene.py). */
export function getOutage(archetype, site, signal) {
  return request('/api/outage', { params: { archetype, site }, signal })
}

/**
 * Real Ring status for linked cameras (backend routes/ring_status.py).
 * `links` looks like ["C1:ava1.ring.device.XXXX"]. The token goes in a
 * header, never the URL, because URLs end up in logs.
 */
export function getRingStatus(token, links, archetype, site, signal) {
  return request('/api/ring/status', {
    params: { link: links, archetype, site },
    headers: { Authorization: `Bearer ${token}` },
    signal,
  })
}

/**
 * The newest picture a Ring camera has saved (backend routes/ring_cameras.py). Resolves with
 * { url, capturedAt }: url is a blob: address for an <img>, only valid in this tab. Call
 * URL.revokeObjectURL(url) when done. The picture is not kept on the server.
 */
export async function getRingSnapshot(token, deviceId, signal) {
  let res
  try {
    res = await fetch(`${BASE}/api/ring/snapshot?device=${encodeURIComponent(deviceId)}`, {
      headers: { Authorization: `Bearer ${token}` },
      signal,
    })
  } catch (err) {
    if (err.name === 'AbortError') throw err
    throw new ApiError(0, "Can't reach the RedHouse API.")
  }
  if (!res.ok) {
    let detail = ''
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail || `Ring answered with status ${res.status}.`)
  }
  const blob = await res.blob()
  return { url: URL.createObjectURL(blob), capturedAt: res.headers.get('X-Captured-At') }
}

// ---- calibrating a camera from its own picture (backend routes/sites.py) ----

const json = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

/** What the camera would see with these matched spots. Saves nothing. */
export function previewCalibration(site, body, signal) {
  return request('/api/calibration/preview', { params: { site }, signal, ...json(body) })
}

/** Keep the calibration. For the demo house a copy is saved: the answer's site_id is then a new site. */
export function saveCalibration(site, body) {
  return request('/api/calibration', { params: { site }, ...json(body) })
}

/** Go back to the guessed view for one camera. */
export function deleteCalibration(site, camera) {
  return request('/api/calibration', { method: 'DELETE', params: { site, camera } })
}

// ---- uploading a site plan (backend routes/sites.py) -----------------------

function form(file, extra = {}) {
  const f = new FormData()
  f.append('file', file, file.name || 'plan.png')
  Object.entries(extra).forEach(([k, v]) => f.append(k, typeof v === 'string' ? v : JSON.stringify(v)))
  return f
}

/** A PDF or image -> a PNG to click on: { png_base64, width, height, pages, page }. */
export function previewPlan(file, page = 0, signal) {
  return request('/api/plans/preview', { method: 'POST', body: form(file, { page: String(page) }), signal })
}

/** Read the plan and report what was found. Saves nothing. */
export function checkSite(image, settings, signal) {
  return request('/api/sites/check', { method: 'POST', body: form(image, { settings: JSON.stringify(settings) }), signal })
}

/** Read the plan and keep the result. Returns { site_id, meta, warnings }. */
export function createSite(image, settings, signal) {
  return request('/api/sites', { method: 'POST', body: form(image, { settings: JSON.stringify(settings) }), signal })
}

export function deleteSite(id) {
  return request(`/api/sites/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
