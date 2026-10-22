import { describe, expect, it } from 'vitest'
import { restoreSession } from '@/engine/restore'

type State = Parameters<typeof restoreSession>[0]
type Doubt = State['doubts'][number]

const spoken = (text: string) => ({ text, audio_url: `/media/doubts/${text.length}_callback.wav`, duration_ms: 1200 })

let counter = 0
function doubt(over: Partial<Doubt> = {}): Doubt {
  counter += 1
  return {
    id: `d${counter}`,
    scene_idx: 0,
    step_idx: 0,
    question: `question ${counter}`,
    intent: 'not_covered',
    target_scene_idx: null,
    answered: false,
    callback: null,
    ...over,
  } as Doubt
}

function state(doubts: Doubt[], over: Partial<State> = {}): State {
  return {
    session_id: 's1',
    completed: false,
    position: { scene_idx: 1, step_idx: 0 },
    answered_checks: [],
    doubts,
    ...over,
  } as State
}

describe('restoreSession', () => {
  it('returns empty collections for a session without doubts', () => {
    const r = restoreSession(state([]), 0)
    expect(r.doubts).toEqual([])
    expect(r.callbacks).toEqual([])
    expect(r.redeliver).toEqual([])
    expect(r.answeredChecks).toEqual([])
  })

  it('copies answered_checks', () => {
    const s = state([], { answered_checks: [0, 2] })
    const r = restoreSession(s, 1)
    expect(r.answeredChecks).toEqual([0, 2])
    expect(r.answeredChecks).not.toBe(s.answered_checks)
  })

  it('rule 1: an answered doubt is answered whatever its intent', () => {
    const cb = spoken('Tumne pucha tha.')
    const s = state([
      doubt({ intent: 'covered_later', target_scene_idx: 2, callback: cb, answered: true }),
      doubt({ intent: 'not_covered', answered: true }),
      doubt({ intent: 'off_topic', answered: true }),
    ])
    const r = restoreSession(s, 0)
    expect(r.doubts.map((d) => d.status)).toEqual(['answered', 'answered', 'answered'])
    expect(r.callbacks).toEqual([])
    expect(r.redeliver).toEqual([])
  })

  it('rule 2: a covered_later doubt whose target is ahead is promised with a callback', () => {
    const cb = spoken('Tumne pucha tha compass ke baare mein.')
    const d = doubt({ intent: 'covered_later', target_scene_idx: 2, callback: cb })
    const r = restoreSession(state([d]), 1)
    expect(r.doubts[0].status).toBe('promised')
    expect(r.doubts[0].targetSceneIdx).toBe(2)
    expect(r.callbacks).toEqual([{ sceneIdx: 2, doubtId: d.id, spoken: cb }])
    expect(r.redeliver).toEqual([])
  })

  it('rule 2: target equal to the resumed scene is not ahead', () => {
    const d = doubt({ intent: 'covered_later', target_scene_idx: 1, callback: spoken('x') })
    const r = restoreSession(state([d]), 1)
    expect(r.doubts[0].status).toBe('answered')
    expect(r.callbacks).toEqual([])
  })

  it('rule 3: a covered_later doubt whose scene was already reached is answered', () => {
    const d = doubt({ intent: 'covered_later', target_scene_idx: 1, callback: spoken('x') })
    const r = restoreSession(state([d]), 2)
    expect(r.doubts[0].status).toBe('answered')
    expect(r.callbacks).toEqual([])
    expect(r.redeliver).toEqual([])
  })

  it('rule 3: a covered_later doubt without a callback is answered even if the target is ahead', () => {
    const d = doubt({ intent: 'covered_later', target_scene_idx: 3, callback: null })
    const r = restoreSession(state([d]), 0)
    expect(r.doubts[0].status).toBe('answered')
    expect(r.callbacks).toEqual([])
  })

  it.each(['not_covered', 'prerequisite', 'confused'])('rule 4: %s is queued for redelivery', (intent) => {
    const d = doubt({ intent })
    const r = restoreSession(state([d]), 0)
    expect(r.doubts[0].status).toBe('queued')
    expect(r.redeliver).toEqual([d.id])
    expect(r.callbacks).toEqual([])
  })

  it('rule 5: repeat is answered', () => {
    const r = restoreSession(state([doubt({ intent: 'repeat' })]), 0)
    expect(r.doubts[0].status).toBe('answered')
    expect(r.redeliver).toEqual([])
  })

  it.each(['off_topic', 'unclear', 'clarify'])('rule 6: %s is dismissed', (intent) => {
    const r = restoreSession(state([doubt({ intent })]), 0)
    expect(r.doubts[0].status).toBe('dismissed')
    expect(r.redeliver).toEqual([])
    expect(r.callbacks).toEqual([])
  })

  it('keeps doubts in the same order as the state and maps id, question and target', () => {
    const d1 = doubt({ intent: 'not_covered', question: 'ek' })
    const d2 = doubt({ intent: 'covered_later', question: 'do', target_scene_idx: 2, callback: spoken('cb') })
    const d3 = doubt({ intent: 'unclear', question: 'teen' })
    const r = restoreSession(state([d1, d2, d3]), 0)
    expect(r.doubts.map((d) => d.id)).toEqual([d1.id, d2.id, d3.id])
    expect(r.doubts.map((d) => d.question)).toEqual(['ek', 'do', 'teen'])
    expect(r.doubts.map((d) => d.intent)).toEqual(['not_covered', 'covered_later', 'unclear'])
    expect(r.doubts.map((d) => d.targetSceneIdx)).toEqual([null, 2, null])
    expect(r.doubts.map((d) => d.status)).toEqual(['queued', 'promised', 'dismissed'])
  })

  it('lists redeliver ids in asked order, skipping non-queued doubts', () => {
    const d1 = doubt({ intent: 'confused' })
    const d2 = doubt({ intent: 'repeat' })
    const d3 = doubt({ intent: 'prerequisite' })
    const d4 = doubt({ intent: 'not_covered', answered: true })
    const d5 = doubt({ intent: 'not_covered' })
    const r = restoreSession(state([d1, d2, d3, d4, d5]), 0)
    expect(r.redeliver).toEqual([d1.id, d3.id, d5.id])
  })

  it('lists callbacks in asked order with their own target scenes', () => {
    const cbA = spoken('A')
    const cbB = spoken('B')
    const d1 = doubt({ intent: 'covered_later', target_scene_idx: 3, callback: cbA })
    const d2 = doubt({ intent: 'not_covered' })
    const d3 = doubt({ intent: 'covered_later', target_scene_idx: 2, callback: cbB })
    const r = restoreSession(state([d1, d2, d3]), 1)
    expect(r.callbacks).toEqual([
      { sceneIdx: 3, doubtId: d1.id, spoken: cbA },
      { sceneIdx: 2, doubtId: d3.id, spoken: cbB },
    ])
  })

  it('does not mutate the state', () => {
    const s = state(
      [
        doubt({ intent: 'covered_later', target_scene_idx: 2, callback: spoken('cb') }),
        doubt({ intent: 'not_covered' }),
        doubt({ intent: 'clarify' }),
      ],
      { answered_checks: [0] },
    )
    const snapshot = JSON.parse(JSON.stringify(s))
    const r = restoreSession(s, 0)
    r.answeredChecks.push(99)
    r.redeliver.push('zzz')
    expect(s).toEqual(snapshot)
  })
})
