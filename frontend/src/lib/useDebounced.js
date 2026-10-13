// useDebounced.js - a value that follows another value, but only after it has
// stopped changing for `ms` milliseconds.
//
// WHY: while someone drags a camera round, the angle changes many times a
// second. The map wedge follows instantly (drawn in the browser), but we
// only ask the simulator for new numbers when they pause.

import { useEffect, useState } from 'react'

export function useDebounced(value, ms) {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}
