import { describe, expect, it } from 'vitest'
import { type Box, marksOf, markBox, textBox, type Mark } from '@/engine/geometry'
import { layoutScene, layoutStep } from '@/engine/layout'
import type { DrawAction } from '@/lib/types'

const text = (id: string, x: number, y: number, t: string, size: 'h1' | 'h2' | 'body' | 'small' = 'body') =>
  ({ type: 'text', id, x, y, text: t, size, color: 'ink' }) as DrawAction
const arrow = (id: string, start: [number, number], end: [number, number], label: string | null = null) =>
  ({ type: 'arrow', id, start, end, color: 'blue', label }) as DrawAction
const circle = (id: string, cx: number, cy: number, r: number, label: string | null = null) =>
  ({ type: 'circle', id, cx, cy, r, color: 'ink', filled: false, label }) as DrawAction

const overlap = (a: Box, b: Box) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h
const texts = (a: DrawAction) => marksOf(a).filter((m): m is Extract<Mark, { k: 'text' }> => m.k === 'text').map(textBox)

describe('layoutStep', () => {
  it('leaves a board without overlaps unchanged', () => {
    const draw = [text('t1', 60, 90, 'Title', 'h1'), text('t2', 60, 200, 'A line'), circle('c1', 1100, 400, 60)]
    const out = layoutStep(draw)
    expect(out.map((a) => ({ ...a, labelPos: undefined }))).toEqual(draw.map((a) => ({ ...a, labelPos: undefined })))
  })

  it('moves text that lands on earlier text to a free line', () => {
    const board = [text('t1', 60, 200, 'Magnetic field is a vector')]
    const [moved] = layoutStep([text('t2', 70, 210, 'Electric field too')], board)
    expect(overlap(texts(moved)[0], texts(board[0])[0])).toBe(false)
    expect(moved.type === 'text' && moved.y).toBeGreaterThan(210)
  })

  it('moves a shape whose strokes cross text', () => {
    const board = [text('t1', 860, 130, 'Propanol: C3H8O')]
    const [moved] = layoutStep([arrow('a1', [1020, 110], [1020, 160])], board)
    const tb = texts(board[0])[0]
    const segs = marksOf(moved).flatMap((m) => (m.k === 'path' ? [markBox(m)] : []))
    expect(segs.some((b) => overlap(b, tb))).toBe(false)
  })

  it('lets shapes overlap each other', () => {
    const draw = [circle('c1', 1000, 400, 80), arrow('a1', [900, 400], [1100, 400])]
    const out = layoutStep(draw)
    expect(out[1].type === 'arrow' && out[1].start).toEqual([900, 400])
  })

  it('puts a label where it does not cross other items', () => {
    const board = [arrow('a1', [800, 300], [1300, 300])]
    const [c] = layoutStep([circle('c1', 1050, 300, 50, 'Compass')], board)
    const label = texts(c).at(-1)!
    const shaft = marksOf(board[0]).map(markBox)
    expect(shaft.some((b) => overlap(b, label))).toBe(false)
  })

  it('pulls text that runs off the board back inside', () => {
    const [t] = layoutStep([text('t1', 1400, 900, 'A long line that runs off the edge')])
    const b = texts(t)[0]
    expect(b.x + b.w).toBeLessThanOrEqual(1600)
    expect(b.y + b.h).toBeLessThanOrEqual(900)
  })

  it('passes highlights through', () => {
    const h = { type: 'highlight', id: 'h1', target: 't1', color: 'orange' } as DrawAction
    expect(layoutStep([h], [text('t1', 60, 90, 'x')])).toEqual([h])
  })

  it('lays out blocks', () => {
    const board = [text('t1', 1000, 300, 'Wire carries current')]
    const charge = { type: 'charge', id: 'q', x: 1050, y: 290, sign: '+', label: 'q', velocity_deg: 0, force_deg: 90, color: 'blue' } as DrawAction
    const [moved] = layoutStep([charge], board)
    expect(moved.type === 'charge' && [moved.x, moved.y]).not.toEqual([1050, 290])
    expect(Object.keys(moved.labelPos ?? {}).sort()).toEqual(['F', 'label', 'v'])
  })
})

describe('blocks', () => {
  it('keeps diagrams out of the title row', () => {
    const axes = { type: 'axes', id: 'x1', x: 860, y: 316, w: 400, h: 300, x_label: 'x', y_label: 'y', color: 'ink' } as DrawAction
    const [moved] = layoutStep([axes])
    const top = Math.min(...marksOf(moved).map((m) => markBox(m).y))
    expect(top).toBeGreaterThanOrEqual(100)
  })

  it('moves every point of a curve together', () => {
    const curve = { type: 'curve', id: 'g1', points: [[860, 100], [960, 50], [1060, 100]], color: 'blue', dashed: false, label: null } as DrawAction
    const [moved] = layoutStep([curve])
    const pts = moved.type === 'curve' ? moved.points : []
    const dy = pts[0][1] - 100
    expect(pts.map((p) => p[1] - dy)).toEqual([100, 50, 100])
    expect(pts.map((p) => p[0])).toEqual([860, 960, 1060])
  })

  it('draws chain groups in a row without overlapping each other', () => {
    const chain = {
      type: 'chain', id: 'm1', x: 860, y: 300, groups: ['CH₃', 'CH', 'OH'], bonds: ['-', '='],
      branches: [{ at: 1, groups: ['CH₃'], dir: 'up', bond: '-' }], color: 'ink',
    } as DrawAction
    const boxes = texts(chain)
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) expect(overlap(boxes[i], boxes[j])).toBe(false)
    // single bond = 1 stroke, double = 2, branch bond = 1
    expect(marksOf(chain).filter((m) => m.k === 'path')).toHaveLength(4)
  })
})

describe('layoutScene', () => {
  it('keeps a label clear of a shape drawn in a later step', () => {
    const steps = [[circle('c1', 1200, 300, 50, 'Wire')], [circle('c2', 1200, 300, 100)]]
    const [[c1], [c2]] = layoutScene(steps)
    const label = texts(c1).at(-1)!
    const ring = marksOf(c2).flatMap((m) => (m.k === 'path' ? m.segs : []))
    const crosses = ring.some(([p, q]) => overlap(label, { x: Math.min(p[0], q[0]), y: Math.min(p[1], q[1]), w: Math.abs(q[0] - p[0]) + 1, h: Math.abs(q[1] - p[1]) + 1 }))
    expect(crosses).toBe(false)
  })

  it('lays out steps in order like layoutStep for text and shapes', () => {
    const steps = [[text('t1', 60, 90, 'Title', 'h1')], [text('t2', 60, 95, 'Overlaps title')]]
    const [[a], [b]] = layoutScene(steps)
    expect(overlap(texts(a)[0], texts(b)[0])).toBe(false)
  })
})
