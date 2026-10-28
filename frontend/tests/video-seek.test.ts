import { describe, expect, it } from 'vitest'

import { canSeek, initialVideoState, type VideoPlayerState } from '@/store/video'

const state = (patch: Partial<VideoPlayerState>): VideoPlayerState => ({ ...initialVideoState, ...patch })

const card = (patch: Partial<NonNullable<VideoPlayerState['checkpoint']>> = {}): VideoPlayerState['checkpoint'] => ({
  idx: 0,
  questionIdx: 0,
  stage: 'answer',
  wrong: [],
  result: null,
  busy: false,
  followup: false,
  ...patch,
})

describe('canSeek', () => {
  it('is live while playing, paused and at the "Got it?" prompt', () => {
    for (const mode of ['playing', 'paused', 'thread_prompt'] as const) expect(canSeek(state({ mode }))).toBe(true)
  })

  it('is closed while listening, routing, answering or ending', () => {
    expect(canSeek(state({ mode: 'playing', listening: true }))).toBe(false)
    expect(canSeek(state({ mode: 'thread_prompt', thinking: true }))).toBe(false)
    for (const mode of ['idle', 'answering', 'thinking', 'summary', 'done'] as const) expect(canSeek(state({ mode }))).toBe(false)
  })

  it('is live on a checkpoint card that is waiting for the student', () => {
    for (const stage of ['answer', 'retry', 'done'] as const) expect(canSeek(state({ mode: 'checkpoint', checkpoint: card({ stage }) }))).toBe(true)
  })

  it('is closed while the card is busy, re-teaching or introducing', () => {
    expect(canSeek(state({ mode: 'checkpoint', checkpoint: card({ busy: true }) }))).toBe(false)
    expect(canSeek(state({ mode: 'checkpoint', checkpoint: card({ stage: 'reteach' }) }))).toBe(false)
    expect(canSeek(state({ mode: 'checkpoint', checkpoint: card({ stage: 'intro' }) }))).toBe(false)
    expect(canSeek(state({ mode: 'checkpoint', checkpoint: null }))).toBe(false)
  })
})
