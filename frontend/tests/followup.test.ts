import { describe, expect, it } from 'vitest'
import { contentBottom, scrollFor, Segment, SEGMENT_GAP, shiftDown } from '@/engine/followup'
import { actionBox, BOARD_H } from '@/engine/geometry'
import type { DrawAction } from '@/lib/types'

const text = (id: string, x: number, y: number, t: string) => ({ type: 'text', id, x, y, text: t, size: 'body', color: 'ink' }) as DrawAction
const circle = (id: string, cx: number, cy: number, r: number, label: string | null = null) =>
  ({ type: 'circle', id, cx, cy, r, color: 'ink', filled: false, label }) as DrawAction

describe('Segment', () => {
  it('leaves the first answer where the LLM put it', () => {
    const seg = new Segment([], 0)
    expect(seg.top).toBe(0)
    expect(seg.separator()).toEqual([])
    const [t] = seg.place([text('t1', 60, 200, 'Field')])
    expect(t).toMatchObject({ id: 't1', y: 200 })
  })

  it('stacks a follow-up below everything on the board, with a separator between', () => {
    const board = [text('t1', 60, 200, 'Field'), circle('c1', 800, 600, 100)]
    const bottom = contentBottom(board)
    expect(bottom).toBeGreaterThanOrEqual(700)
    const seg = new Segment(board, 1)
    expect(seg.top).toBe(bottom + SEGMENT_GAP)
    const [sep] = seg.separator()
    expect(sep.type === 'line' && sep.start[1]).toBeLessThan(seg.top)
    expect(sep.type === 'line' && sep.start[1]).toBeGreaterThan(bottom)
    const [t] = seg.place([text('t1', 60, 80, 'Follow-up')])
    expect(actionBox(t)!.y).toBeGreaterThan(bottom)
  })

  it('renames a follow-up\'s ids and keeps its highlights pointing at its own items', () => {
    const seg = new Segment([text('t1', 60, 200, 'Field')], 1)
    const [t] = seg.place([text('t1', 60, 80, 'Again')])
    const [h] = seg.place([{ type: 'highlight', id: 'h1', target: 't1', color: 'orange' } as DrawAction])
    expect(t.id).toBe('f1-t1')
    expect(h.type === 'highlight' && h.target).toBe('f1-t1')
  })

  it('lays out later clips of a segment against its earlier ones', () => {
    const seg = new Segment([text('t0', 60, 300, 'Above')], 1)
    const [a] = seg.place([text('t1', 60, 200, 'One line')])
    const [b] = seg.place([text('t2', 60, 200, 'Another line')])
    expect(actionBox(b)!.y).toBeGreaterThan(actionBox(a)!.y)
  })
})

describe('scrollFor', () => {
  it('does not scroll while new items fit', () => {
    expect(scrollFor(0, [text('t1', 60, 400, 'Fits')])).toBe(0)
  })

  it('scrolls just enough to show items below the board, and never back up', () => {
    const low = [text('t1', 60, BOARD_H + 200, 'Below')]
    const s = scrollFor(0, low)
    expect(s).toBeGreaterThan(200)
    expect(s).toBeLessThan(400)
    expect(scrollFor(s + 500, low)).toBe(s + 500)
  })
})

describe('shiftDown', () => {
  it('moves label positions with the shape', () => {
    const c = { ...circle('c1', 500, 400, 50, 'N'), labelPos: { label: [560, 400] } } as DrawAction
    const moved = shiftDown(c, 1000)
    expect(moved).toMatchObject({ cy: 1400, labelPos: { label: [560, 1400] } })
  })
})
