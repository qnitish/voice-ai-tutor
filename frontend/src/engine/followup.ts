/**
 * Follow-ups on the doubt board. The LLM draws every answer for a blank 1600×900 board, so each answer of a doubt
 * conversation is laid out in its own segment and then placed below the earlier ones; the board scrolls up to make
 * room, keeping as much of the earlier answer in view as it can.
 */
import { actionBox, BOARD_H, BOARD_W } from '@/engine/geometry'
import { layoutStep, translate } from '@/engine/layout'
import type { DrawAction, Point } from '@/lib/types'
import { setPlayer, usePlayer } from '@/store/player'

/** Space between one answer and the follow-up below it (the separator sits in the middle) */
export const SEGMENT_GAP = 64
/** Kept clear below the newest line when the board scrolls */
const BOTTOM_MARGIN = 40

/** Lowest point of anything on the board (0 when empty). */
export function contentBottom(board: DrawAction[]): number {
  let bottom = 0
  for (const a of board) {
    const b = actionBox(a)
    if (b) bottom = Math.max(bottom, b.y + b.h)
  }
  return bottom
}

/** Move an action down by dy, labels included (layout.translate drops their positions). */
export function shiftDown(a: DrawAction, dy: number): DrawAction {
  const out = translate(a, 0, dy)
  if (a.labelPos) {
    out.labelPos = Object.fromEntries(Object.entries(a.labelPos).map(([k, [x, y]]) => [k, [x, y + dy] as Point]))
  }
  return out
}

/** Scroll that keeps these new actions in view: never back up, only as far down as needed. */
export function scrollFor(current: number, draw: DrawAction[]): number {
  const bottom = contentBottom(draw)
  return Math.max(current, bottom + BOTTOM_MARGIN - BOARD_H)
}

/** Board scroll speed when it lifts for a follow-up (board units per ms), and its bounds */
const LIFT_SPEED = 0.9
const LIFT_MS: [number, number] = [450, 1100]

const easeInOut = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2)

/** Scroll the board up to `to`, eased like a sheet being pulled up (`stopped`: the player was closed). */
export function liftBoard(to: number, stopped: () => boolean): Promise<void> {
  const from = usePlayer.getState().boardScroll
  if (to <= from) return Promise.resolve()
  const ms = Math.min(LIFT_MS[1], Math.max(LIFT_MS[0], (to - from) / LIFT_SPEED))
  const t0 = performance.now()
  return new Promise((resolve) => {
    const tick = (now: number) => {
      if (stopped()) return resolve()
      const t = Math.min(1, (now - t0) / ms)
      setPlayer({ boardScroll: from + (to - from) * easeInOut(t) })
      if (t < 1) requestAnimationFrame(tick)
      else resolve()
    }
    requestAnimationFrame(tick)
  })
}

/** One answer's area on the stacked board. Segment 0 is the doubt's own answer, at the top. */
export class Segment {
  /** The segment's actions in its own coordinates, to lay out later clips against */
  private local: DrawAction[] = []
  /** Ids renamed in this segment, so its highlights and motions still find their items */
  private ids = new Map<string, string>()
  readonly top: number
  private readonly prefix: string

  constructor(board: DrawAction[], index: number) {
    this.top = index ? contentBottom(board) + SEGMENT_GAP : 0
    // Every answer numbers its items t1, t2…: later segments get their own ids
    this.prefix = index ? `f${index}-` : ''
  }

  /** A dashed line between the earlier answer and this one. */
  separator(): DrawAction[] {
    if (!this.top) return []
    const y = this.top - SEGMENT_GAP / 2
    return [{ type: 'line', id: `${this.prefix}sep`, start: [60, y], end: [BOARD_W - 60, y], color: 'ink', dashed: true }]
  }

  /** Lay out a clip's actions inside the segment and return them at its place on the board. */
  place(draw: DrawAction[]): DrawAction[] {
    const laid = layoutStep(
      draw.map((a) => this.rename(a)),
      this.local,
    )
    this.local.push(...laid)
    return this.top ? laid.map((a) => shiftDown(a, this.top)) : laid
  }

  private rename(a: DrawAction): DrawAction {
    if (!this.prefix) return a
    const ref = (id: string) => this.ids.get(id) ?? id
    const out = { ...a, id: `${this.prefix}${a.id}` } as DrawAction
    if (out.type === 'highlight') out.target = ref(out.target)
    if (out.type === 'motion') out.tracks = out.tracks.map((t) => ({ ...t, target: ref(t.target) }))
    this.ids.set(a.id, out.id)
    return out
  }
}
