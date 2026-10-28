import { describe, expect, it } from 'vitest'

import { actionBox, BOARD_W, FONT_SIZE, textWidth } from '@/engine/geometry'
import { markRight, markWrong, note, quizLayout, wrap } from '@/engine/video/quizBoard'
import type { DrawAction } from '@/lib/types'

const LONG =
  'A hot air balloon starts from rest on the ground and rises with constant acceleration a₀ = 2 m/s². At t₀ = 5 s a man inside it drops a packet. Just after release, what are the packet\'s initial velocity and acceleration?'
const OPTIONS = ['Zero; 2 m/s² upward', '10 m/s upward; 2 m/s² upward', '10 m/s upward; g downward, which is a much longer option that has to wrap onto a second line of the board', 'Zero; g downward']

const boxes = (actions: DrawAction[]) => actions.map((a) => actionBox(a)!)
const ids = (actions: DrawAction[]) => actions.map((a) => a.id)

describe('wrap', () => {
  it('keeps every line within the width and loses no word', () => {
    const lines = wrap(LONG, 700)
    expect(lines.length).toBeGreaterThan(1)
    for (const line of lines) expect(textWidth(line, FONT_SIZE.body)).toBeLessThanOrEqual(700)
    expect(lines.join(' ')).toBe(LONG)
  })
})

describe('quizLayout', () => {
  const layout = quizLayout(LONG, OPTIONS, 2, 3)
  const all = [...layout.question, ...layout.options.flat()]

  it('writes the heading, then the question, then the options A–D in order', () => {
    expect(layout.question[0]).toMatchObject({ type: 'text', text: 'Question 2 of 3', size: 'h2' })
    const tops = layout.boxes.map((b) => b.y)
    expect([...tops].sort((a, b) => a - b)).toEqual(tops)
    const lastQuestionLine = boxes(layout.question).at(-1)!
    expect(layout.boxes[0].y).toBeGreaterThan(lastQuestionLine.y + lastQuestionLine.h)
    layout.options.forEach((lines, j) => expect(lines[0]).toMatchObject({ text: expect.stringMatching(new RegExp(`^${'ABCD'[j]}\\. `)) }))
  })

  it('wraps a long option onto more lines, indented under its text', () => {
    expect(layout.options[2].length).toBeGreaterThan(1)
    const [first, second] = layout.options[2] as Extract<DrawAction, { type: 'text' }>[]
    expect(second.x).toBeGreaterThan(first.x)
  })

  it('stays inside the board width, with unique ids', () => {
    for (const b of boxes(all)) {
      expect(b.x).toBeGreaterThanOrEqual(0)
      expect(b.x + b.w).toBeLessThanOrEqual(BOARD_W)
    }
    expect(new Set(ids(all)).size).toBe(all.length)
  })

  it('marks a wrong pick and the right option around their own text', () => {
    const cross = markWrong(layout.boxes[2], 2)
    expect(cross.filter((a) => a.type === 'line')).toHaveLength(layout.options[2].length) // one strike per line
    const [frame, tick] = markRight(layout.boxes[1], 1)
    const box = layout.boxes[1]
    expect(frame).toMatchObject({ type: 'rect', color: 'green' })
    const f = actionBox(frame)!
    expect(f.x).toBeLessThan(box.x)
    expect(f.y + f.h).toBeGreaterThan(box.y + box.h)
    expect(tick).toMatchObject({ text: '✓', color: 'green' })
  })

  it('writes a note below the options, and the next note below that', () => {
    const hint = note('hint', 'Hint: think about what the packet shares with the balloon at the moment it is released.', layout.bottom, 'orange')
    expect(actionBox(hint.actions[0])!.y).toBeGreaterThan(layout.bottom)
    const why = note('why', 'Explanation.', hint.bottom, 'green')
    expect(actionBox(why.actions[0])!.y).toBeGreaterThan(hint.bottom)
  })
})

describe('quizLayout with a picture', () => {
  const portrait = { url: '/media/q6.png', w: 764, h: 1000 }
  const wide = { url: '/media/q1.png', w: 1000, h: 667 }

  it('puts the picture on the right, inside the board and within its size limit', () => {
    for (const image of [portrait, wide]) {
      const { question } = quizLayout(LONG, OPTIONS, 1, 2, image)
      const img = question.find((a) => a.type === 'image')!
      const b = actionBox(img)!
      expect(b.x + b.w).toBeLessThanOrEqual(BOARD_W - 60)
      expect(b.w).toBeLessThanOrEqual(560)
      expect(b.h).toBeLessThanOrEqual(520)
      expect(b.w / b.h).toBeCloseTo(image.w / image.h, 1)
    }
  })

  it('keeps the question and options clear of the picture', () => {
    const layout = quizLayout(LONG, OPTIONS, 1, 2, portrait)
    const img = actionBox(layout.question.find((a) => a.type === 'image')!)!
    const text = [...layout.question.filter((a) => a.type === 'text'), ...layout.options.flat()]
    for (const b of boxes(text)) expect(b.x + b.w).toBeLessThan(img.x)
  })

  it('writes notes below the picture when it reaches lower than the options', () => {
    const layout = quizLayout('Short question?', ['A1', 'B1', 'C1', 'D1'], 1, 2, portrait)
    const img = actionBox(layout.question.find((a) => a.type === 'image')!)!
    const hint = note('hint', 'Hint: think again.', layout.bottom, 'orange')
    expect(actionBox(hint.actions[0])!.y).toBeGreaterThan(img.y + img.h)
  })
})

