/**
 * What the board shows at one moment, as a flat list in paint order. Board (SVG) and SkiaBoard (CanvasKit) both draw
 * this list, so the two renderers can only differ in how they paint a mark, never in what or where.
 */
import type { Color, DrawAction } from '@/lib/types'
import { actionBox, type Box, type Mark, marksOf } from '@/engine/geometry'
import { motionFrame, type Shown } from '@/engine/motion'
import type { TimedAction } from '@/store/player'

export const BOARD_COLORS: Record<Color, string> = {
  ink: '#1f2937',
  blue: '#2563eb',
  red: '#dc2626',
  green: '#15803d',
  orange: '#f59e0b',
}

export const BOARD_BG = { lesson: '#ffffff', doubt: '#fffbeb' } as const
export const GRID = { step: 40, color: '#e5e7eb', opacity: 0.6 } as const

export type PathMark = Extract<Mark, { k: 'path' }>

/**
 * - mark: one stroke or text at progress p (strokes draw themselves, text is written left to right, labels fade in),
 *   moved by (dx, dy) when its item has been moved by a motion
 * - box: highlighter box around text-like items
 * - glow: highlighter traced over a shape's stroke
 */
export type FrameItem =
  | { k: 'mark'; key: string; mark: Mark; p: number; dx: number; dy: number }
  | { k: 'box'; key: string; box: Box; color: Color; p: number }
  | { k: 'glow'; key: string; mark: PathMark; color: Color; p: number; dx: number; dy: number }

// Highlights box these in; other actions get their strokes traced
const TEXT_LIKE = new Set<DrawAction['type']>(['text', 'flow', 'chain'])

export const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

export function progress(t: TimedAction, clipTime: number) {
  return clamp01((clipTime - t.start) / Math.max(1, t.end - t.start))
}

export function frameOf(drawn: DrawAction[], active: TimedAction[], clipTime: number): FrameItem[] {
  const shown: Shown[] = [
    ...drawn.map((action) => ({ action, p: 1, live: false })),
    ...active.filter((t) => clipTime >= t.start).map((t) => ({ action: t.action, p: progress(t, clipTime), live: true })),
  ]
  const byId = new Map(shown.map((s) => [s.action.id, s.action]))
  const motion = motionFrame(shown)
  const offset = (id: string) => motion.offsets.get(id) ?? [0, 0]

  // Paint order: highlights, motion trails, items, live motion arrows
  const highlights: FrameItem[] = []
  const trails: FrameItem[] = []
  const items: FrameItem[] = []
  const arrows: FrameItem[] = []
  const motionMarks = (marks: Mark[] | undefined, id: string, out: FrameItem[]) =>
    marks?.forEach((mark, i) => out.push({ k: 'mark', key: `${id}-${out.length}-${i}`, mark, p: 1, dx: 0, dy: 0 }))

  for (const { action: a, p } of shown) {
    if (a.type === 'highlight') {
      const target = byId.get(a.target)
      if (!target) continue
      const [dx, dy] = offset(target.id)
      if (!TEXT_LIKE.has(target.type)) {
        marksOf(target).forEach((m, i) => {
          if (m.k === 'path') highlights.push({ k: 'glow', key: `${a.id}-${i}`, mark: m, color: a.color, p, dx, dy })
        })
        continue
      }
      const box = actionBox(target)
      if (box) highlights.push({ k: 'box', key: a.id, box: { ...box, x: box.x + dx, y: box.y + dy }, color: a.color, p })
      continue
    }
    if (a.type === 'motion') {
      motionMarks(motion.under.get(a.id), a.id, trails)
      motionMarks(motion.over.get(a.id), a.id, arrows)
      continue
    }
    // A block's marks are drawn one after another within the action's time
    const [dx, dy] = offset(a.id)
    const marks = marksOf(a)
    const n = marks.length
    marks.forEach((mark, i) => items.push({ k: 'mark', key: `${a.id}-${i}`, mark, p: clamp01(p * n - i), dx, dy }))
  }
  return [...highlights, ...trails, ...items, ...arrows]
}
