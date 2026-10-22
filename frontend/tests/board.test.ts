import { describe, expect, it } from 'vitest'
import { boardBefore, progressPercent } from '@/engine/board'

type Draw = Parameters<typeof boardBefore>[0]['steps'][number]['draw'][number]

const action = (id: string) => ({ type: 'text', id, text: id, x: 0, y: 0 }) as unknown as Draw

const sceneWith = (...draws: Draw[][]) => ({ steps: draws.map((draw) => ({ draw })) })

describe('boardBefore', () => {
  const s = sceneWith([action('a1'), action('a2')], [action('b1')], [], [action('d1')])

  it('returns [] for stepIdx 0', () => {
    expect(boardBefore(s, 0)).toEqual([])
  })

  it('returns [] for a negative stepIdx', () => {
    expect(boardBefore(s, -3)).toEqual([])
  })

  it('returns the actions of the steps strictly before stepIdx, in order', () => {
    expect(boardBefore(s, 1)).toEqual([action('a1'), action('a2')])
    expect(boardBefore(s, 2)).toEqual([action('a1'), action('a2'), action('b1')])
    expect(boardBefore(s, 3)).toEqual([action('a1'), action('a2'), action('b1')])
  })

  it('returns all actions when stepIdx is the number of steps', () => {
    expect(boardBefore(s, 4)).toEqual([action('a1'), action('a2'), action('b1'), action('d1')])
  })

  it('returns all actions when stepIdx is beyond the end', () => {
    expect(boardBefore(s, 99)).toEqual([action('a1'), action('a2'), action('b1'), action('d1')])
  })

  it('returns [] for a scene without steps', () => {
    expect(boardBefore({ steps: [] }, 5)).toEqual([])
  })

  it('does not mutate the scene', () => {
    const scene = sceneWith([action('x')], [action('y')])
    const out = boardBefore(scene, 2)
    out.push(action('z'))
    expect(scene.steps[0].draw).toEqual([action('x')])
    expect(scene.steps[1].draw).toEqual([action('y')])
  })
})

describe('progressPercent', () => {
  // step counts 2, 3, 5 -> 10 steps in total
  const lesson = {
    scenes: [{ steps: [1, 2] }, { steps: [1, 2, 3] }, { steps: [1, 2, 3, 4, 5] }],
  }

  it('is 0 at the very start', () => {
    expect(progressPercent(lesson, 0, 0)).toBe(0)
  })

  it('counts the steps of earlier scenes plus stepIdx', () => {
    expect(progressPercent(lesson, 0, 1)).toBe(10)
    expect(progressPercent(lesson, 1, 0)).toBe(20)
    expect(progressPercent(lesson, 1, 2)).toBe(40)
    expect(progressPercent(lesson, 2, 4)).toBe(90)
  })

  it('rounds to the nearest integer', () => {
    const three = { scenes: [{ steps: [1, 2, 3] }] }
    expect(progressPercent(three, 0, 1)).toBe(33)
    expect(progressPercent(three, 0, 2)).toBe(67)
  })

  it('returns 0 when the lesson has no steps', () => {
    expect(progressPercent({ scenes: [] }, 0, 0)).toBe(0)
    expect(progressPercent({ scenes: [{ steps: [] }] }, 0, 0)).toBe(0)
  })

  it('clamps to 100', () => {
    expect(progressPercent(lesson, 2, 5)).toBe(100)
    expect(progressPercent(lesson, 2, 50)).toBe(100)
    expect(progressPercent(lesson, 7, 0)).toBe(100)
  })

  it('clamps to 0', () => {
    expect(progressPercent(lesson, 0, -4)).toBe(0)
  })
})
