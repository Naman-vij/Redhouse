// useRing.js
//
// Everything about the live Ring connection, kept in one place:
//   1. connect  - read the devices on the account with a pasted token
//   2. link     - say "Ring device X is camera C1 on this map"
//   3. watch    - re-check Ring every 30 seconds and report each linked
//                 camera's real online/offline state
//
// SECURITY: the token is held in a ref (plain memory) only. It is never put
// in localStorage, never in the URL, and it vanishes when the tab closes.
// It is sent to OUR backend in an Authorization header, and nowhere else.

import { useCallback, useEffect, useRef, useState } from 'react'
import { getRingStatus } from '../api'

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
  const [state, setState] = useState(START)
  const tokenRef = useRef('')
  const linksRef = useRef({}) // { C1: "<ring device id>" }

  const check = useCallback(
    async (links) => {
      setState((s) => ({ ...s, busy: true }))
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
    setState(START)
  }, [])

  // While watching, look again every 30 seconds.
  useEffect(() => {
    if (state.phase !== 'watching') return undefined
    const timer = setInterval(() => check(linksRef.current), POLL_MS)
    return () => clearInterval(timer)
  }, [state.phase, check])

  const liveNames = state.cameras.filter((c) => c.source === 'ring').map((c) => c.name)

  return { ...state, liveNames, connect, link, unlink, refresh, disconnect, pollSeconds: POLL_MS / 1000 }
}
