// sites.js - which sites this browser has uploaded, and which one the address bar points at.
//
// The server keeps no list of sites (ids are secret), so this browser remembers the ones it made.
// localStorage can be blocked or empty (private windows), so every read and write is wrapped:
// the page works without it, it just forgets your sites.

const KEY = 'redhouse.sites'

export function recentSites() {
  try {
    const list = JSON.parse(window.localStorage.getItem(KEY) || '[]')
    return Array.isArray(list) ? list.filter((s) => s && typeof s.id === 'string') : []
  } catch {
    return []
  }
}

export function rememberSite({ id, name, mode }) {
  try {
    const rest = recentSites().filter((s) => s.id !== id)
    window.localStorage.setItem(KEY, JSON.stringify([{ id, name, mode, at: Date.now() }, ...rest].slice(0, 12)))
  } catch {
    /* storage unavailable: nothing to do */
  }
}

export function forgetSite(id) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(recentSites().filter((s) => s.id !== id)))
  } catch {
    /* storage unavailable */
  }
}
