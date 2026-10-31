import { useCallback, useEffect, useRef, useState } from 'react'

const HIDE_MS = 3000

/** Video controls that come up on a tap (or mouse move) and fade away again after a few seconds. */
export function useAutoHide() {
  const [shown, setShown] = useState(true)
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined)

  const hide = useCallback(() => {
    clearTimeout(timer.current)
    setShown(false)
  }, [])

  const wake = useCallback(() => {
    clearTimeout(timer.current)
    setShown(true)
    timer.current = setTimeout(() => setShown(false), HIDE_MS)
  }, [])

  useEffect(() => () => clearTimeout(timer.current), [])

  return { shown, wake, hide, toggle: () => (shown ? hide() : wake()) }
}
