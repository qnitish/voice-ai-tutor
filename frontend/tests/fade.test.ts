import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { FADE_MS, fadeTo } from '@/engine/fade'

/** An element whose volume writes are recorded. */
function trackedElement(initial: number) {
  const writes: number[] = []
  let value = initial
  const el = {} as { volume: number }
  Object.defineProperty(el, 'volume', {
    get: () => value,
    set: (v: number) => {
      value = v
      writes.push(v)
    },
    enumerable: true,
  })
  return { el, writes }
}

/** Whether a promise has already settled, checked without advancing timers. */
async function isSettled(p: Promise<unknown>): Promise<boolean> {
  let settled = false
  p.then(() => (settled = true))
  await Promise.resolve()
  await Promise.resolve()
  return settled
}

describe('fadeTo', () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('exports FADE_MS = 150', () => {
    expect(FADE_MS).toBe(150)
  })

  it('sets the volume immediately when ms <= 0', async () => {
    const el = { volume: 1 }
    const p = fadeTo(el, 0.25, 0)
    expect(el.volume).toBe(0.25)
    await p
    expect(el.volume).toBe(0.25)
  })

  it('sets the volume immediately for a negative duration', async () => {
    const el = { volume: 0 }
    const p = fadeTo(el, 1, -10)
    expect(el.volume).toBe(1)
    await p
  })

  it('reaches the target exactly after the duration', async () => {
    const el = { volume: 1 }
    const p = fadeTo(el, 0, 150)
    await vi.advanceTimersByTimeAsync(150)
    await p
    expect(el.volume).toBe(0)
  })

  it('fades up as well as down', async () => {
    const el = { volume: 0 }
    const p = fadeTo(el, 1, 200)
    await vi.advanceTimersByTimeAsync(200)
    await p
    expect(el.volume).toBe(1)
  })

  it('does not resolve before the duration has elapsed', async () => {
    const el = { volume: 1 }
    const p = fadeTo(el, 0, 1000)
    await vi.advanceTimersByTimeAsync(400)
    expect(await isSettled(p)).toBe(false)
    expect(el.volume).toBeGreaterThan(0)
    await vi.advanceTimersByTimeAsync(600)
    expect(await isSettled(p)).toBe(true)
  })

  it('keeps the volume between the start value and the target while fading', async () => {
    const { el, writes } = trackedElement(0.8)
    const p = fadeTo(el, 0.2, 500)
    await vi.advanceTimersByTimeAsync(500)
    await p
    expect(writes.length).toBeGreaterThan(0)
    for (const v of writes) {
      expect(v).toBeLessThanOrEqual(0.8)
      expect(v).toBeGreaterThanOrEqual(0.2)
    }
  })

  it('changes the volume gradually, not in a single jump', async () => {
    const { el, writes } = trackedElement(1)
    const p = fadeTo(el, 0, 300)
    await vi.advanceTimersByTimeAsync(300)
    await p
    expect(writes.length).toBeGreaterThanOrEqual(2)
  })

  it('is roughly linear: about half way at half time', async () => {
    const el = { volume: 1 }
    const p = fadeTo(el, 0, 1000)
    await vi.advanceTimersByTimeAsync(500)
    expect(el.volume).toBeGreaterThan(0.3)
    expect(el.volume).toBeLessThan(0.7)
    await vi.advanceTimersByTimeAsync(500)
    await p
  })

  it('clamps the target to [0, 1]', async () => {
    const el = { volume: 0.5 }
    const p1 = fadeTo(el, 1.7, 100)
    await vi.advanceTimersByTimeAsync(100)
    await p1
    expect(el.volume).toBe(1)
    const p2 = fadeTo(el, -0.4, 100)
    await vi.advanceTimersByTimeAsync(100)
    await p2
    expect(el.volume).toBe(0)
  })

  it('uses setTimeout, not requestAnimationFrame', async () => {
    const raf = vi.fn()
    vi.stubGlobal('requestAnimationFrame', raf)
    try {
      const el = { volume: 1 }
      const p = fadeTo(el, 0, 150)
      await vi.advanceTimersByTimeAsync(150)
      await p
      expect(raf).not.toHaveBeenCalled()
      expect(el.volume).toBe(0)
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('a newer fade cancels the older one: the older promise resolves', async () => {
    const el = { volume: 1 }
    const older = fadeTo(el, 0, 1000)
    await vi.advanceTimersByTimeAsync(300)
    const newer = fadeTo(el, 1, 200)
    await vi.advanceTimersByTimeAsync(1)
    expect(await isSettled(older)).toBe(true)
    await vi.advanceTimersByTimeAsync(200)
    await newer
  })

  it('a newer fade cancels the older one: only the newer fade changes the volume', async () => {
    const { el, writes } = trackedElement(1)
    const older = fadeTo(el, 0, 1000)
    await vi.advanceTimersByTimeAsync(300)
    const atSwitch = el.volume
    const newer = fadeTo(el, 1, 200)
    const writesBefore = writes.length
    await vi.advanceTimersByTimeAsync(200)
    await newer
    await older
    expect(el.volume).toBe(1)
    for (const v of writes.slice(writesBefore)) {
      expect(v).toBeGreaterThanOrEqual(atSwitch - 1e-9)
    }
    // the older fade must not keep running after the newer one finished
    const writesAfterNewer = writes.length
    await vi.advanceTimersByTimeAsync(1000)
    expect(el.volume).toBe(1)
    expect(writes.length).toBe(writesAfterNewer)
  })

  it('fades on different elements do not cancel each other', async () => {
    const a = { volume: 1 }
    const b = { volume: 0 }
    const pa = fadeTo(a, 0, 300)
    const pb = fadeTo(b, 1, 300)
    await vi.advanceTimersByTimeAsync(300)
    await Promise.all([pa, pb])
    expect(a.volume).toBe(0)
    expect(b.volume).toBe(1)
  })
})
