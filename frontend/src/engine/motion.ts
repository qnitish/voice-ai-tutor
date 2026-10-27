/**
 * Kinematics of `motion` actions. The LLM only gives initial velocity and acceleration (board px/s and px/s², +y up,
 * like the angles); every frame is computed here from x = x₀ + vt + ½at², so a falling ball is always physically right.
 * Moved items keep their final offset, and a later motion of the same item starts from there.
 */
import type { DrawAction, Point } from '@/lib/types'
import { arrowMarks, BODY_R, bodyMarks, CHARGE_R, type Mark, markBox, ring, stroke, union } from '@/engine/geometry'

type Motion = Extract<DrawAction, { type: 'motion' }>
type Track = Motion['tracks'][number]

/** Velocity arrow length per px/s of speed. */
export const VEL_SCALE = 0.3
const GHOST_OPACITY = 0.3

/** Displacement after t seconds, in board coordinates (y down). */
export function displacement(tr: Track, t: number): Point {
  return [tr.v[0] * t + 0.5 * tr.a[0] * t * t, -(tr.v[1] * t + 0.5 * tr.a[1] * t * t)]
}

/** Velocity after t seconds, in board coordinates (y down). */
export function velocity(tr: Track, t: number): Point {
  return [tr.v[0] + tr.a[0] * t, -(tr.v[1] + tr.a[1] * t)]
}

/** Centre of an item and its half-height (how far its bottom is below the centre). */
export function anchorOf(a: DrawAction): { at: Point; r: number } | null {
  switch (a.type) {
    case 'body':
      return { at: [a.x, a.y], r: BODY_R }
    case 'charge':
      return { at: [a.x, a.y], r: CHARGE_R }
    case 'circle':
    case 'ring':
      return { at: [a.cx, a.cy], r: a.r }
    default: {
      const b = union(bodyMarks(a).map(markBox))
      return b ? { at: [b.x + b.w / 2, b.y + b.h / 2], r: b.h / 2 } : null
    }
  }
}

/** Seconds until an item whose bottom starts `drop` px above a ground line touches it; Infinity if it never does. */
export function landingTime(tr: Track, drop: number): number {
  if (drop < 0) return Infinity
  // Board y moves by -(vy t + ½ ay t²); it lands when that equals `drop`: ½ay t² + vy t + drop = 0
  const [vy, ay] = [tr.v[1], tr.a[1]]
  if (ay === 0) return vy < 0 ? drop / -vy : Infinity
  const disc = vy * vy - 2 * ay * drop
  if (disc < 0) return Infinity
  const roots = [(-vy - Math.sqrt(disc)) / ay, (-vy + Math.sqrt(disc)) / ay].filter((t) => t > 1e-6)
  return roots.length ? Math.min(...roots) : Infinity
}

const plus = (p: Point, d: Point): Point => [p[0] + d[0], p[1] + d[1]]

/** A faint outline of the item at `at` (strobe snapshot). */
function ghost(target: DrawAction, at: Point, r: number, m: Motion): Mark {
  const [x, y] = at
  const mark =
    target.type === 'body' && target.shape === 'box'
      ? stroke([[x - r, y - r], [x + r, y - r], [x + r, y + r], [x - r, y + r]], m.color, 3, {}, true)
      : ring(x, y, r, m.color, 3)
  return { ...mark, opacity: GHOST_OPACITY } as Mark
}

export interface Shown {
  action: DrawAction
  /** 0 -> 1 progress of the action on its clip's timeline (1 = finished) */
  p: number
  /** Still on the playing clip's timeline (live velocity arrows show only then) */
  live: boolean
}

export interface MotionFrame {
  /** Current offset of every moved item */
  offsets: Map<string, Point>
  /** Per motion id: trail and ghosts (drawn under the items) and live arrows (drawn over them) */
  under: Map<string, Mark[]>
  over: Map<string, Mark[]>
}

/** Where every moved item is, and what its motions draw, for the items shown in this order. */
export function motionFrame(shown: Shown[]): MotionFrame {
  const byId = new Map(shown.map((s) => [s.action.id, s.action]))
  const offsets = new Map<string, Point>()
  const under = new Map<string, Mark[]>()
  const over = new Map<string, Mark[]>()
  for (const { action: m, p, live } of shown) {
    if (m.type !== 'motion') continue
    const below: Mark[] = []
    const above: Mark[] = []
    const ground = m.ground ? byId.get(m.ground) : undefined
    m.tracks.forEach((tr, i) => {
      const target = byId.get(tr.target)
      const anchor = target && anchorOf(target)
      if (!target || !anchor) return
      const before = offsets.get(tr.target) ?? [0, 0]
      const start = plus(anchor.at, before)
      const land = ground?.type === 'ground' ? landingTime(tr, ground.y - (start[1] + anchor.r)) : Infinity
      const end = Math.min(m.seconds, land)
      const t = Math.min(p * m.seconds, end)
      offsets.set(tr.target, plus(before, displacement(tr, t)))
      const at = (s: number) => plus(start, displacement(tr, s))

      if (m.trace && t > 0) {
        const n = Math.max(2, Math.ceil((40 * t) / end))
        below.push(stroke(Array.from({ length: n + 1 }, (_, i) => at((t * i) / n)), m.color, 3, { dashed: true }))
      }
      // Ghosts only for the item being studied (the first track); on every track they pile up in one column
      for (let k = 0; i === 0 && k < m.ghosts; k++) {
        const s = (end * k) / m.ghosts
        if (s <= t) below.push(ghost(target, at(s), anchor.r, m))
      }
      if (m.show_velocity && live && p < 1 && t < end) {
        const v = velocity(tr, t)
        const speed = Math.hypot(v[0], v[1])
        const len = speed * VEL_SCALE
        if (len > 12) {
          const dir: Point = [v[0] / speed, v[1] / speed]
          const pos = at(t)
          const from = plus(pos, [dir[0] * (anchor.r + 4), dir[1] * (anchor.r + 4)])
          const tip = plus(from, [dir[0] * len, dir[1] * len])
          above.push(...arrowMarks(from, tip, 'blue'))
          // Beside the tip, on the side away from the path
          above.push({ k: 'text', x: tip[0] + 22 + Math.abs(dir[1]) * 6, y: tip[1] + 10, text: 'v', px: 28, weight: 400, color: 'blue', anchor: 'middle', label: true })
        }
      }
    })
    under.set(m.id, below)
    over.set(m.id, above)
  }
  return { offsets, under, over }
}
