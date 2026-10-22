import { describe, expect, it } from 'vitest'
import { twinSlot, wrongNext } from '@/engine/remedy'

type Q = Parameters<typeof wrongNext>[0]

const spoken = (text: string) => ({ text, audio_url: `/media/${text}.wav`, duration_ms: 800 })
const clip = { say: 'Ek aur tarike se.', draw: [], audio_url: '/media/re.wav', duration_ms: 1000 }

function q(over: Partial<Q> = {}): Q {
  return {
    question: 'Q?',
    say: 'Q?',
    options: ['a', 'b', 'c', 'd'],
    correct_index: 1,
    concept: 'poles',
    explanation: 'Because.',
    audio_url: '/media/q.wav',
    ...over,
  } as Q
}

describe('wrongNext', () => {
  it('retries after a first wrong answer when hints exist', () => {
    expect(wrongNext(q({ why_wrong: [spoken('a'), null, spoken('c'), spoken('d')], reteach: [clip] }), 'first')).toBe('retry')
  })

  it('needs at least one real hint to retry', () => {
    expect(wrongNext(q({ why_wrong: [null, null, null, null], reteach: [clip] }), 'first')).toBe('reteach')
  })

  it('re-teaches after a wrong retry', () => {
    expect(wrongNext(q({ why_wrong: [spoken('a'), null, null, null], reteach: [clip] }), 'retry')).toBe('reteach')
  })

  it('re-teaches straight away without hints', () => {
    expect(wrongNext(q({ reteach: [clip, clip] }), 'first')).toBe('reteach')
  })

  it('reveals when there is nothing to re-teach', () => {
    expect(wrongNext(q(), 'first')).toBe('reveal')
    expect(wrongNext(q({ reteach: [] }), 'first')).toBe('reveal')
    expect(wrongNext(q({ why_wrong: [spoken('a'), null, null, null] }), 'retry')).toBe('reveal')
  })
})

describe('twinSlot', () => {
  it('asks the twin after the next scene', () => {
    expect(twinSlot(4, 0)).toBe(1)
    expect(twinSlot(4, 2)).toBe(3)
  })

  it('asks the last scene twin at the end', () => {
    expect(twinSlot(4, 3)).toBe('end')
    expect(twinSlot(1, 0)).toBe('end')
  })
})
