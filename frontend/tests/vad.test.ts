import { describe, expect, it } from 'vitest'
import { SilenceDetector, pressKind, rms } from '@/engine/vad'

describe('rms', () => {
  it('returns 0 for empty input', () => {
    expect(rms([])).toBe(0)
    expect(rms(new Float32Array(0))).toBe(0)
  })

  it('returns the absolute value of a single sample', () => {
    expect(rms([0.5])).toBeCloseTo(0.5, 10)
    expect(rms([-0.5])).toBeCloseTo(0.5, 10)
  })

  it('computes the root mean square', () => {
    expect(rms([3, 4])).toBeCloseTo(Math.sqrt(12.5), 10)
    expect(rms([1, 1, 1, 1])).toBeCloseTo(1, 10)
    expect(rms([0.1, -0.1, 0.1, -0.1])).toBeCloseTo(0.1, 10)
  })

  it('returns 0 for silence', () => {
    expect(rms(new Float32Array(16))).toBe(0)
  })

  it('accepts typed arrays', () => {
    expect(rms(new Float32Array([0.6, -0.8]))).toBeCloseTo(Math.sqrt(0.5), 6)
  })
})

describe('SilenceDetector', () => {
  it('starts in waiting while the level is below the threshold', () => {
    const d = new SilenceDetector()
    expect(d.push(0, 0)).toBe('waiting')
    expect(d.push(0.01, 100)).toBe('waiting')
  })

  it('returns speaking when the level reaches the threshold', () => {
    const d = new SilenceDetector()
    expect(d.push(0.02, 0)).toBe('speaking')
  })

  it('uses the default threshold 0.02', () => {
    const d = new SilenceDetector()
    expect(d.push(0.0199, 0)).toBe('waiting')
    expect(d.push(0.02, 10)).toBe('speaking')
  })

  it('reports no_speech after noSpeechMs without any speech (default 6000)', () => {
    const d = new SilenceDetector()
    expect(d.push(0, 0)).toBe('waiting')
    expect(d.push(0, 5999)).toBe('waiting')
    expect(d.push(0, 6000)).toBe('no_speech')
  })

  it('stays speaking during short gaps', () => {
    const d = new SilenceDetector()
    d.push(0.1, 0)
    expect(d.push(0, 500)).toBe('speaking')
    expect(d.push(0, 1199)).toBe('speaking')
  })

  it('reports end after silenceMs of silence following speech (default 1200)', () => {
    const d = new SilenceDetector()
    d.push(0.1, 0)
    d.push(0.1, 1000)
    expect(d.push(0, 2199)).toBe('speaking')
    expect(d.push(0, 2200)).toBe('end')
  })

  it('measures silence from the last voice, not the first', () => {
    const d = new SilenceDetector()
    d.push(0.1, 0)
    d.push(0, 1000)
    d.push(0.1, 1100) // voice again resets the silence clock
    expect(d.push(0, 2200)).toBe('speaking')
    expect(d.push(0, 2300)).toBe('end')
  })

  it('does not report no_speech once speech was heard', () => {
    const d = new SilenceDetector({ silenceMs: 100000 })
    d.push(0.1, 0)
    expect(d.push(0, 7000)).toBe('speaking')
  })

  it('reports max once maxMs has elapsed (default 25000), even while speaking', () => {
    const d = new SilenceDetector()
    d.push(0.1, 0)
    expect(d.push(0.1, 24999)).toBe('speaking')
    expect(d.push(0.1, 25000)).toBe('max')
  })

  it('max takes precedence over no_speech', () => {
    const d = new SilenceDetector({ noSpeechMs: 1000, maxMs: 1000 })
    d.push(0, 0)
    expect(d.push(0, 1000)).toBe('max')
  })

  it('max takes precedence over end', () => {
    const d = new SilenceDetector({ silenceMs: 500, maxMs: 2000 })
    d.push(0.1, 0)
    d.push(0.1, 1400)
    expect(d.push(0, 2000)).toBe('max')
  })

  it('t0 is the time of the first push, not zero', () => {
    const d = new SilenceDetector({ noSpeechMs: 1000 })
    expect(d.push(0, 5000)).toBe('waiting')
    expect(d.push(0, 5999)).toBe('waiting')
    expect(d.push(0, 6000)).toBe('no_speech')
  })

  it('keeps returning end after it was reached', () => {
    const d = new SilenceDetector({ silenceMs: 100 })
    d.push(0.1, 0)
    expect(d.push(0, 100)).toBe('end')
    expect(d.push(0.5, 150)).toBe('end')
    expect(d.push(0.5, 90000)).toBe('end')
  })

  it('keeps returning no_speech after it was reached', () => {
    const d = new SilenceDetector({ noSpeechMs: 100 })
    d.push(0, 0)
    expect(d.push(0, 100)).toBe('no_speech')
    expect(d.push(0.9, 110)).toBe('no_speech')
  })

  it('keeps returning max after it was reached', () => {
    const d = new SilenceDetector({ maxMs: 100 })
    d.push(0.1, 0)
    expect(d.push(0.1, 100)).toBe('max')
    expect(d.push(0, 100000)).toBe('max')
  })

  it('honours custom threshold, silenceMs and noSpeechMs', () => {
    const d = new SilenceDetector({ threshold: 0.5, silenceMs: 300, noSpeechMs: 2000, maxMs: 10000 })
    expect(d.push(0.4, 0)).toBe('waiting')
    expect(d.push(0.5, 10)).toBe('speaking')
    expect(d.push(0.1, 309)).toBe('speaking')
    expect(d.push(0.1, 310)).toBe('end')
  })

  it('honours a custom noSpeechMs', () => {
    const d = new SilenceDetector({ noSpeechMs: 2000 })
    d.push(0, 0)
    expect(d.push(0, 1999)).toBe('waiting')
    expect(d.push(0, 2000)).toBe('no_speech')
  })

  it('each detector is independent', () => {
    const a = new SilenceDetector({ silenceMs: 100 })
    const b = new SilenceDetector({ silenceMs: 100 })
    a.push(0.1, 0)
    expect(b.push(0, 0)).toBe('waiting')
    expect(a.push(0, 100)).toBe('end')
    expect(b.push(0, 100)).toBe('waiting')
  })
})

describe('pressKind', () => {
  it('is a tap when shorter than tapMaxMs (default 300)', () => {
    expect(pressKind(1000, 1100)).toBe('tap')
    expect(pressKind(1000, 1299)).toBe('tap')
    expect(pressKind(1000, 1000)).toBe('tap')
  })

  it('is a hold at or above tapMaxMs', () => {
    expect(pressKind(1000, 1300)).toBe('hold')
    expect(pressKind(1000, 5000)).toBe('hold')
  })

  it('accepts a custom tapMaxMs', () => {
    expect(pressKind(0, 499, 500)).toBe('tap')
    expect(pressKind(0, 500, 500)).toBe('hold')
  })
})
