import { useEffect, useState } from 'react'

type Lockable = ScreenOrientation & { lock?: (o: 'landscape') => Promise<void>; unlock?: () => void }

/**
 * Full screen for the lecture. The page lays itself out over the whole viewport (CSS, so it works on iPhone, which
 * only lets a bare <video> go full screen and would hide the mic and the board), and where the browser allows it
 * the document also enters real full screen and the phone turns to landscape. Esc or leaving browser full screen
 * ends it; so does leaving the page.
 */
export function useFullscreen() {
  const [full, setFull] = useState(false)

  useEffect(() => {
    if (!full) return
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    const onChange = () => {
      if (!document.fullscreenElement) setFull(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFull(false)
    }
    document.addEventListener('fullscreenchange', onChange)
    window.addEventListener('keydown', onKey)
    return () => {
      document.body.style.overflow = overflow
      document.removeEventListener('fullscreenchange', onChange)
      window.removeEventListener('keydown', onKey)
      if (document.fullscreenElement) void document.exitFullscreen().catch(() => {})
      try {
        ;(screen.orientation as Lockable | undefined)?.unlock?.()
      } catch {
        // not supported: nothing was locked
      }
    }
  }, [full])

  /** Must run inside the click: browsers only grant full screen to a user gesture. */
  function enter() {
    setFull(true)
    // The whole document, not the player, so toasts (rendered outside it) still show
    document.documentElement
      .requestFullscreen?.()
      .then(() => (screen.orientation as Lockable | undefined)?.lock?.('landscape'))
      .catch(() => {
        // iPhone, or orientation lock refused: the CSS layout is full screen enough
      })
  }

  return { full, toggle: () => (full ? setFull(false) : enter()) }
}
