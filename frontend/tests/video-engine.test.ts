import { describe, expect, it } from 'vitest'

import { restoreDoubts, statusFor } from '@/engine/video/doubts'
import type { VideoSessionState } from '@/lib/types'

describe('statusFor', () => {
  it('maps how a routed question is shown in the doubt list', () => {
    expect(statusFor('now')).toBe('queued')
    expect(statusFor('chapter_end')).toBe('queued')
    expect(statusFor('target_chapter')).toBe('promised')
    expect(statusFor('replay')).toBe('answered')
    expect(statusFor('none')).toBe('dismissed')
  })
})

describe('restoreDoubts', () => {
  const state: VideoSessionState = {
    session_id: 's1',
    completed: false,
    position_ms: 120_000,
    answered: [],
    doubts: [
      { id: 'a', at_ms: 10_000, thread_id: 't1', turn: 0, question: 'Why up?', intent: 'confused', answered: true },
      { id: 'b', at_ms: 20_000, thread_id: 't2', turn: 0, question: 'Later?', intent: 'covered_later', answered: false },
      { id: 'c', at_ms: 30_000, thread_id: 't3', turn: 0, question: 'Again', intent: 'repeat', answered: false },
      { id: 'd', at_ms: 40_000, thread_id: 't4', turn: 0, question: 'Lost', intent: 'not_covered', answered: false },
      { id: 'e', at_ms: 50_000, thread_id: null, turn: 0, question: '?', intent: 'something_new', answered: false },
    ],
  }

  it('keeps id, time, thread and question', () => {
    const [a] = restoreDoubts(state)
    expect(a).toEqual({ id: 'a', atMs: 10_000, threadId: 't1', question: 'Why up?', intent: 'confused', status: 'answered' })
  })

  it('counts answered, covered_later and repeat doubts as answered, the rest as dismissed', () => {
    expect(restoreDoubts(state).map((d) => d.status)).toEqual(['answered', 'answered', 'answered', 'dismissed', 'dismissed'])
  })

  it('drops an intent the player does not know', () => {
    expect(restoreDoubts(state).at(-1)?.intent).toBeNull()
  })
})
