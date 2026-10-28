import { describe, expect, it } from 'vitest'
import { frameOf } from '@/engine/frame'
import { displacement, landingTime, motionFrame } from '@/engine/motion'
import { layoutScene } from '@/engine/layout'
import type { DrawAction } from '@/lib/types'

const ball = (id: string, x: number, y: number): DrawAction => ({ type: 'body', id, x, y, shape: 'ball', label: null, vectors: [], color: 'ink' })
const ground = (y: number): DrawAction => ({ type: 'ground', id: 'gr1', x: 860, y, w: 600, label: null })
const motion = (over: Partial<Extract<DrawAction, { type: 'motion' }>> = {}): DrawAction => ({
  type: 'motion',
  id: 'm1',
  tracks: [{ target: 'o1', v: [0, 0], a: [0, -300] }],
  seconds: 2,
  ground: null,
  trace: true,
  ghosts: 0,
  show_velocity: false,
  color: 'blue',
  ...over,
})
const shown = (actions: DrawAction[], p = 1) => actions.map((action) => ({ action, p: action.type === 'motion' ? p : 1, live: true }))

describe('kinematics', () => {
  it('falls ½gt² downward on the board (y grows)', () => {
    expect(displacement({ target: 'o1', v: [0, 0], a: [0, -300] }, 2)).toEqual([0, 600])
  })

  it('a throw up rises first', () => {
    const [, dy] = displacement({ target: 'o1', v: [0, 300], a: [0, -300] }, 0.5)
    expect(dy).toBeLessThan(0)
  })

  it('lands when the bottom reaches the ground', () => {
    // drop 150 px at 300 px/s²: ½·300·t² = 150 → t = 1
    expect(landingTime({ target: 'o1', v: [0, 0], a: [0, -300] }, 150)).toBeCloseTo(1)
  })

  it('never lands when moving away at constant speed', () => {
    expect(landingTime({ target: 'o1', v: [0, 100], a: [0, 0] }, 150)).toBe(Infinity)
  })
})

describe('motionFrame', () => {
  it('moves the target by the progress of the motion', () => {
    const f = motionFrame(shown([ball('o1', 1000, 200), motion()], 0.5))
    // t = 1 s of 2: ½·300·1² = 150
    expect(f.offsets.get('o1')).toEqual([0, 150])
  })

  it('stops a falling item on the ground', () => {
    const f = motionFrame(shown([ball('o1', 1000, 200), ground(800), motion({ ground: 'gr1', seconds: 5 })]))
    // bottom of the ball (radius 22) rests on y = 800
    expect(f.offsets.get('o1')![1]).toBeCloseTo(800 - 22 - 200)
  })

  it('starts a second motion where the first ended', () => {
    const second = motion({ id: 'm2', tracks: [{ target: 'o1', v: [100, 0], a: [0, 0] }], seconds: 1 })
    const f = motionFrame(shown([ball('o1', 1000, 200), motion({ seconds: 1 }), second]))
    expect(f.offsets.get('o1')).toEqual([100, 150])
  })

  it('draws ghosts only for the time already shown', () => {
    const f = motionFrame(shown([ball('o1', 1000, 200), motion({ ghosts: 4, trace: false })], 0.5))
    // snapshots every 0.5 s of 2 s; at t = 1 s those at 0, 0.5 and 1 are shown
    expect(f.under.get('m1')).toHaveLength(3)
  })
})

describe('frameOf', () => {
  it('moves every mark of the target and draws the trail under it', () => {
    const items = frameOf([ball('o1', 1000, 200), motion()], [], 0)
    const body = items.filter((i) => i.k === 'mark' && i.key.startsWith('o1'))
    expect(body.every((i) => i.k === 'mark' && i.dy === 600)).toBe(true)
    const trail = items.findIndex((i) => i.key.startsWith('m1'))
    expect(trail).toBeLessThan(items.findIndex((i) => i.key.startsWith('o1')))
  })
})

describe('layout with motion', () => {
  it('keeps later text off the path of a falling item', () => {
    const text: DrawAction = { type: 'text', id: 't1', x: 980, y: 500, text: 'falls', size: 'body', color: 'ink' }
    const [, [laid]] = layoutScene([[ball('o1', 1000, 200), motion()], [text]])
    expect(laid.type === 'text' && (laid.y !== 500 || laid.x !== 980)).toBe(true)
  })
})
