import { describe, expect, it } from 'vitest'

import { initialVideoState, micState, type VideoMode, type VideoPlayerState } from '@/store/video'

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

describe('micState', () => {
  it('opens the mic while the lecture plays, is paused, answers or waits at the prompt', () => {
    for (const mode of ['playing', 'paused', 'answering', 'thread_prompt'] satisfies VideoMode[]) {
      expect(micState(state({ mode })).enabled, mode).toBe(true)
    }
  })

  it('closes the mic before the lesson starts, while the summary is made and once it is done', () => {
    for (const mode of ['idle', 'summary', 'done', 'listening', 'thinking'] satisfies VideoMode[]) {
      expect(micState(state({ mode })).enabled, mode).toBe(false)
    }
  })

  it('closes the mic as soon as a checkpoint starts, before its card is up', () => {
    expect(micState(state({ mode: 'checkpoint', checkpoint: null })).enabled).toBe(false)
  })

  it('opens the mic at a checkpoint only at the follow-up step while nothing is busy', () => {
    expect(micState(state({ mode: 'checkpoint', checkpoint: card() })).enabled).toBe(false)
    expect(micState(state({ mode: 'checkpoint', checkpoint: card({ stage: 'done', followup: true }) })).enabled).toBe(true)
    expect(micState(state({ mode: 'checkpoint', checkpoint: card({ stage: 'done', followup: true, busy: true }) })).enabled).toBe(false)
  })

  it('reports listening and thinking, and closes the mic while a question is routed', () => {
    expect(micState(state({ mode: 'listening', listening: true }))).toEqual({ enabled: false, listening: true, thinking: false })
    expect(micState(state({ mode: 'answering', thinking: true })).enabled).toBe(false)
  })
})
