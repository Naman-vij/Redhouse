// useRing.js
//
// Everything about the live Ring connection, kept in one place:
//   1. connect  - read the devices on the account with a pasted token
//   2. link     - say "Ring device X is camera C1 on this map"
//   3. watch    - re-check Ring every 30 seconds and report each linked
//                 camera's real online/offline state
//
//   4. picture  - fetch a linked camera's latest saved picture (for calibration)
//
// SECURITY: the token is held in plain memory only. It is never put in
// localStorage, never in the URL, and it vanishes when the tab closes.
// It is sent to OUR backend in an Authorization header, and nowhere else.
//
// NOTE: the page is rebuilt when it switches site. Calibrating the demo house
// saves a COPY of it as a new site, and the person should not have to paste
// their token again for that. So the token and links also sit in `memory`
// below (still plain memory, gone with the tab), and carryRingTo(id) says
// "keep them for this next site". Any other site switch starts disconnected,
// because camera names on another site mean other cameras.

import { useCallback, useEffect, useRef, useState } from 'react'
import { getRingSnapshot, getRingStatus } from '../api'

const memory = { token: '', links: {}, carry: undefined }

/** Keep the Ring connection when the page moves to site `id` (a calibrated copy of this one). */
export function carryRingTo(id) {
  memory.carry = id ?? null
}

const POLL_MS = 30_000

const START = {
  phase: 'idle', // idle -> connected -> watching
  devices: [], // every Ring device on the account
  cameras: [], // one entry per map camera: "ring" (live) or "simulated"
  lastChecked: null, // Date of the last successful check
  error: null,
  busy: false,
}

export function useRing(archetype, site) {
  // Decided once, when the page for this site first appears.
  const [carried] = useState(() => Boolean(memory.token) && memory.carry === (site ?? null))
  const [state, setState] = useState(carried ? { ...START, busy: true, phase: 'connected' } : START)
  const tokenRef = useRef(carried ? memory.token : '')
  const linksRef = useRef(carried ? memory.links : {}) // { C1: "<ring device id>" }

  const check = useCallback(
    async (links) => {
      setState((s) => ({ ...s, busy: true }))
      memory.token = tokenRef.current
      memory.links = links
      try {
        const pairs = Object.entries(links).map(([cam, dev]) => `${cam}:${dev}`)
        const data = await getRingStatus(tokenRef.current, pairs, archetype, site)
        const linked = pairs.length > 0
        setState((s) => ({
          ...s,
          busy: false,
          error: null,
          devices: data.ring_devices,
          cameras: linked ? data.cameras : [],
          lastChecked: new Date(),
          phase: linked ? 'watching' : 'connected',
        }))
      } catch (err) {
        if (err.status === 401) {
          // The token is no good any more (they last 30 minutes): start over.
          tokenRef.current = ''
          linksRef.current = {}
          memory.token = ''
          memory.links = {}
          setState({ ...START, error: err.message })
        } else {
          setState((s) => ({ ...s, busy: false, error: err.message }))
        }
      }
    },
    [archetype, site],
  )

  const connect = useCallback(
    (token) => {
      tokenRef.current = token.trim()
      linksRef.current = {}
      return check({})
    },
    [check],
  )

  const link = useCallback(
    (cameraName, deviceId) => {
      linksRef.current = { ...linksRef.current, [cameraName]: deviceId }
      return check(linksRef.current)
    },
    [check],
  )

  const unlink = useCallback(
    (cameraName) => {
      const { [cameraName]: _removed, ...rest } = linksRef.current
      linksRef.current = rest
      return check(rest)
    },
    [check],
  )

  const refresh = useCallback(() => check(linksRef.current), [check])

  const disconnect = useCallback(() => {
    tokenRef.current = ''
    linksRef.current = {}
    memory.token = ''
    memory.links = {}
    setState(START)
  }, [])

  // A connection carried over from the previous page: check it straight away. Otherwise forget any old one.
  useEffect(() => {
    memory.carry = undefined
    if (carried) check(linksRef.current)
    else {
      memory.token = ''
      memory.links = {}
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  /** The latest saved picture from the Ring device linked to `cameraName`. Resolves { url, capturedAt }. */
  const picture = useCallback((cameraName) => {
    const deviceId = linksRef.current[cameraName]
    if (!tokenRef.current || !deviceId) return Promise.reject(new Error('Link this camera to a Ring device first.'))
    return getRingSnapshot(tokenRef.current, deviceId)
  }, [])

  // While watching, look again every 30 seconds.
  useEffect(() => {
    if (state.phase !== 'watching') return undefined
    const timer = setInterval(() => check(linksRef.current), POLL_MS)
    return () => clearInterval(timer)
  }, [state.phase, check])

  const liveNames = state.cameras.filter((c) => c.source === 'ring').map((c) => c.name)

  return { ...state, liveNames, connect, link, unlink, refresh, disconnect, picture, pollSeconds: POLL_MS / 1000 }
}
