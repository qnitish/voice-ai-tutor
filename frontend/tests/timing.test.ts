import { describe, expect, it } from 'vitest'
import {
  FALLBACK_REWIND_MS,
  MAX_REWIND_MS,
  resumePoint,
  sentenceStarts,
  splitSentences,
} from '@/engine/timing'

// Sentences of exact character lengths (terminator included), joined by a space.
const sentence = (len: number, end = '.') => 'a'.repeat(len - 1) + end

describe('splitSentences', () => {
  it('splits after . ? ! followed by whitespace', () => {
    expect(splitSentences('Dekho yeh wire hai. Current badhao! Kya hoga?')).toEqual([
      'Dekho yeh wire hai.',
      'Current badhao!',
      'Kya hoga?',
    ])
  })

  it('does not split on a decimal point', () => {
    expect(splitSentences('Value 2.5 hai. Theek?')).toEqual(['Value 2.5 hai.', 'Theek?'])
  })

  it('splits after the Devanagari danda', () => {
    expect(splitSentences('Yeh magnet hai। Yeh pole hai।')).toEqual(['Yeh magnet hai।', 'Yeh pole hai।'])
  })

  it('splits on newlines as whitespace', () => {
    expect(splitSentences('Pehla.\nDoosra.')).toEqual(['Pehla.', 'Doosra.'])
  })

  it('trims each part', () => {
    expect(splitSentences('  Pehla.   Doosra?  ')).toEqual(['Pehla.', 'Doosra?'])
  })

  it('keeps a terminator at the very end without a trailing empty part', () => {
    expect(splitSentences('Sirf ek.')).toEqual(['Sirf ek.'])
  })

  it('returns the trimmed text when there is no terminator', () => {
    expect(splitSentences('  bina terminator ke text  ')).toEqual(['bina terminator ke text'])
  })

  it('returns [] for empty text', () => {
    expect(splitSentences('')).toEqual([])
  })

  it('returns [] for whitespace-only text', () => {
    expect(splitSentences('   \n  ')).toEqual([])
  })

  it('does not split when the terminator is not followed by whitespace', () => {
    expect(splitSentences('a.b c')).toEqual(['a.b c'])
  })
})

describe('sentenceStarts', () => {
  it('starts at 0 and shares the duration by character length', () => {
    const text = `${sentence(10)} ${sentence(30)}`
    expect(sentenceStarts(text, 4000)).toEqual([0, 1000])
  })

  it('gives three proportional starts', () => {
    const text = `${sentence(10)} ${sentence(20)} ${sentence(10)}`
    expect(sentenceStarts(text, 8000)).toEqual([0, 2000, 6000])
  })

  it('gives [0] for a single sentence', () => {
    expect(sentenceStarts('Sirf ek sentence hai.', 3000)).toEqual([0])
  })

  it('gives [0] for text without a terminator', () => {
    expect(sentenceStarts('koi terminator nahi', 3000)).toEqual([0])
  })

  it('returns [] for empty text', () => {
    expect(sentenceStarts('', 3000)).toEqual([])
    expect(sentenceStarts('   ', 3000)).toEqual([])
  })

  it('has one entry per sentence, non-decreasing and within the duration', () => {
    const text = 'Ek. Do do. Teen teen teen. Chaar?'
    const starts = sentenceStarts(text, 5000)
    expect(starts).toHaveLength(4)
    expect(starts[0]).toBe(0)
    for (let i = 1; i < starts.length; i++) {
      expect(starts[i]).toBeGreaterThanOrEqual(starts[i - 1])
      expect(starts[i]).toBeLessThanOrEqual(5000)
    }
  })
})

describe('resumePoint', () => {
  // starts: [0, 2000, 6000] over 8000 ms
  const text = `${sentence(10)} ${sentence(20)} ${sentence(10)}`
  const duration = 8000

  it('exports the rewind constants', () => {
    expect(MAX_REWIND_MS).toBe(6000)
    expect(FALLBACK_REWIND_MS).toBe(2000)
  })

  it('returns 0 when paused at 0', () => {
    expect(resumePoint(text, duration, 0)).toBe(0)
  })

  it('returns 0 when paused at a negative time', () => {
    expect(resumePoint(text, duration, -500)).toBe(0)
  })

  it('returns the start of the sentence that was playing', () => {
    expect(resumePoint(text, duration, 2500)).toBe(2000)
    expect(resumePoint(text, duration, 7000)).toBe(6000)
  })

  it('returns 0 inside the first sentence', () => {
    expect(resumePoint(text, duration, 500)).toBe(0)
  })

  it('treats a pause exactly at a sentence start as that sentence', () => {
    expect(resumePoint(text, duration, 2000)).toBe(2000)
  })

  it('rewinds to the sentence start when exactly MAX_REWIND_MS into it', () => {
    const long = sentence(40)
    expect(resumePoint(long, 20000, MAX_REWIND_MS)).toBe(0)
  })

  it('falls back to a short rewind when the sentence start is too far back', () => {
    const long = sentence(40)
    expect(resumePoint(long, 20000, 9000)).toBe(9000 - FALLBACK_REWIND_MS)
    expect(resumePoint(long, 20000, MAX_REWIND_MS + 1)).toBe(MAX_REWIND_MS + 1 - FALLBACK_REWIND_MS)
  })

  it('treats text without sentences as starting at 0', () => {
    expect(resumePoint('', 10000, 3000)).toBe(0)
    expect(resumePoint('', 10000, 8000)).toBe(6000)
  })

  it('never returns more than pausedAtMs and never a negative value', () => {
    const samples = [1, 100, 1999, 2000, 2001, 5999, 6000, 6001, 7999, 8000, 12000]
    for (const pausedAt of samples) {
      const r = resumePoint(text, duration, pausedAt)
      expect(r).toBeLessThanOrEqual(pausedAt)
      expect(r).toBeGreaterThanOrEqual(0)
    }
  })
})
