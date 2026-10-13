// useLocation.js - the page address, as state. Two things live in it:
//   ?site=<id>   show an uploaded site (no id: the demo house)
//   ?new         show the upload wizard
// Small enough that a router library would be more code than this.

import { useCallback, useEffect, useState } from 'react'

export function useLocation() {
  const [search, setSearch] = useState(window.location.search)

  useEffect(() => {
    const onPop = () => setSearch(window.location.search)
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  const navigate = useCallback((to) => {
    window.history.pushState(null, '', `${window.location.pathname}${to}`)
    setSearch(window.location.search)
    window.scrollTo(0, 0)
  }, [])

  return [new URLSearchParams(search), navigate]
}
