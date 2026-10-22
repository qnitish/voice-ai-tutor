/** Volume fades, so interrupting the tutor doesn't cut a word in half. */

export const FADE_MS = 150
const TICK_MS = 16

interface Volume {
  volume: number
}

// The fade currently driving each element; a newer fade replaces it
const running = new WeakMap<Volume, () => void>()

export function fadeTo(el: Volume, target: number, ms: number): Promise<void> {
  const to = Math.min(1, Math.max(0, target))
  running.get(el)?.()
  running.delete(el)
  if (ms <= 0) {
    el.volume = to
    return Promise.resolve()
  }
  const from = el.volume
  return new Promise((resolve) => {
    let elapsed = 0
    let timer: ReturnType<typeof setTimeout> | undefined
    const finish = () => {
      clearTimeout(timer)
      if (running.get(el) === finish) running.delete(el)
      resolve()
    }
    // The last tick lands exactly on `ms`
    const schedule = () => {
      const wait = Math.min(TICK_MS, ms - elapsed)
      timer = setTimeout(() => {
        elapsed += wait
        if (elapsed >= ms) {
          el.volume = to
          finish()
        } else {
          el.volume = from + (to - from) * (elapsed / ms)
          schedule()
        }
      }, wait)
    }
    running.set(el, finish)
    schedule()
  })
}
