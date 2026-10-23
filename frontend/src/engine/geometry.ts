/**
 * Draw actions -> marks (strokes and text). The board renders marks and the layout pass
 * checks them for overlaps, so both see the same shapes. Blocks (wire, charge, compass, …)
 * expand here into ordinary strokes with fixed proportions.
 */
import type { Bond, Color, DrawAction, Point } from '@/lib/types'

export const BOARD_W = 1600
export const BOARD_H = 900
export const FONT_STACK = "Kalam, 'Noto Sans Math', 'Noto Sans', sans-serif"
export const FONT_SIZE = { h1: 52, h2: 38, body: 30, small: 22 } as const
export const LABEL_PX = 28

export type Seg = [Point, Point]
export interface Box {
  x: number
  y: number
  w: number
  h: number
}

export type Mark =
  | { k: 'path'; d: string; color: Color; width: number; dashed?: boolean; fill?: Color; fillOpacity?: number; segs: Seg[] }
  | { k: 'text'; x: number; y: number; text: string; px: number; weight: number; color: Color; anchor: 'start' | 'middle'; label?: boolean }

// ---- text measurement ----

let ctx: CanvasRenderingContext2D | null | undefined

/** Width of `text` in the board font; an estimate where there is no canvas (tests). */
export function textWidth(text: string, px: number, weight = 400): number {
  if (ctx === undefined) ctx = typeof document !== 'undefined' ? document.createElement('canvas').getContext('2d') : null
  if (!ctx) return text.length * px * 0.47
  ctx.font = `${weight} ${px}px ${FONT_STACK}`
  return ctx.measureText(text).width
}

export function textBox(m: Extract<Mark, { k: 'text' }>): Box {
  const w = textWidth(m.text, m.px, m.weight)
  return { x: m.anchor === 'middle' ? m.x - w / 2 : m.x, y: m.y - m.px * 0.8, w, h: m.px * 1.05 }
}

// ---- small helpers ----

const rad = (deg: number) => (deg * Math.PI) / 180
/** Point at `r` from (cx, cy) in direction `deg` (counter-clockwise from +x, screen y down). */
export const polar = (cx: number, cy: number, r: number, deg: number): Point => [cx + r * Math.cos(rad(deg)), cy - r * Math.sin(rad(deg))]

function poly(points: Point[], closed = false): Seg[] {
  const segs: Seg[] = []
  for (let i = 1; i < points.length; i++) segs.push([points[i - 1], points[i]])
  if (closed && points.length > 2) segs.push([points[points.length - 1], points[0]])
  return segs
}

const pathOf = (points: Point[], closed = false) =>
  points.map(([x, y], i) => `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`).join(' ') + (closed ? ' Z' : '')

function stroke(points: Point[], color: Color, width = 4, extra: Partial<Extract<Mark, { k: 'path' }>> = {}, closed = false): Mark {
  return { k: 'path', d: pathOf(points, closed), color, width, segs: poly(points, closed), ...extra }
}

function ellipsePoints(cx: number, cy: number, rx: number, ry: number, from = 0, to = 360, n = 32): Point[] {
  const pts: Point[] = []
  for (let i = 0; i <= n; i++) {
    const a = rad(from + ((to - from) * i) / n)
    pts.push([cx + rx * Math.cos(a), cy - ry * Math.sin(a)])
  }
  return pts
}

function ring(cx: number, cy: number, r: number, color: Color, width = 4, fill?: Color, fillOpacity = 1): Mark {
  const pts = ellipsePoints(cx, cy, r, r)
  return {
    k: 'path',
    d: `M${cx + r},${cy} A${r},${r} 0 1,0 ${cx - r},${cy} A${r},${r} 0 1,0 ${cx + r},${cy}`,
    color,
    width,
    segs: poly(pts),
    ...(fill ? { fill, fillOpacity } : {}),
  }
}

/** Arrow shaft and head from a to b. */
function arrowMarks(a: Point, b: Point, color: Color, width = 4, head = 22): Mark[] {
  const ang = Math.atan2(b[1] - a[1], b[0] - a[0])
  const h1: Point = [b[0] - head * Math.cos(ang - 0.45), b[1] - head * Math.sin(ang - 0.45)]
  const h2: Point = [b[0] - head * Math.cos(ang + 0.45), b[1] - head * Math.sin(ang + 0.45)]
  return [stroke([a, b], color, width), stroke([h1, b, h2], color, width)]
}

/** A small chevron at p pointing along `deg` (screen angle in radians). */
function chevron(p: Point, ang: number, color: Color, size = 16): Mark {
  const tip: Point = [p[0] + (size / 2) * Math.cos(ang), p[1] + (size / 2) * Math.sin(ang)]
  const h1: Point = [tip[0] - size * Math.cos(ang - 0.5), tip[1] - size * Math.sin(ang - 0.5)]
  const h2: Point = [tip[0] - size * Math.cos(ang + 0.5), tip[1] - size * Math.sin(ang + 0.5)]
  return stroke([h1, tip, h2], color, 4)
}

// ---- labels ----

/** A label whose position the layout chooses from `cands` (label centres, best first). */
export interface LabelSlot {
  key: string
  text: string
  color: Color
  cands: Point[]
}

/** Label centres around a point at distance `r`, in the given directions (degrees). */
function around(p: Point, r: number, text: string, dirs: number[]): Point[] {
  const w = textWidth(text, LABEL_PX)
  const h = LABEL_PX
  // Every direction close by first, then farther out
  return [6, 30, 64].flatMap((gap) =>
    dirs.map((deg): Point => {
      const c = Math.cos(rad(deg))
      const s = Math.sin(rad(deg))
      return [p[0] + c * (r + gap + w / 2), p[1] - s * (r + gap + h / 2)]
    }),
  )
}

const ALL_DIRS = [0, 45, 315, 90, 270, 135, 225, 180]

/** Directions perpendicular to a segment first, then along it. */
function sideDirs(a: Point, b: Point): number[] {
  const deg = (Math.atan2(-(b[1] - a[1]), b[0] - a[0]) * 180) / Math.PI
  return [deg + 90, deg - 90, deg + 45, deg - 45, deg + 135, deg - 135]
}

export function labelSlots(a: DrawAction): LabelSlot[] {
  const slot = (key: string, text: string | null | undefined, color: Color, cands: Point[]): LabelSlot[] =>
    text ? [{ key, text, color, cands }] : []
  switch (a.type) {
    case 'arrow': {
      const mid: Point = [(a.start[0] + a.end[0]) / 2, (a.start[1] + a.end[1]) / 2]
      return slot('label', a.label, a.color, around(mid, 6, a.label ?? '', sideDirs(a.start, a.end)))
    }
    case 'circle':
      return slot('label', a.label, a.color, around([a.cx, a.cy], a.r, a.label ?? '', ALL_DIRS))
    case 'angle': {
      const sweep = (((a.end_deg - a.start_deg) % 360) + 360) % 360
      const mid = a.start_deg + sweep / 2
      return slot('label', a.label, a.color, around([a.cx, a.cy], a.r + 4, a.label ?? '', [mid, mid + 20, mid - 20]))
    }
    case 'wire': {
      const mid: Point = [(a.start[0] + a.end[0]) / 2, (a.start[1] + a.end[1]) / 2]
      return slot('current', a.current, 'blue', around(mid, 10, a.current ?? '', sideDirs(a.start, a.end)))
    }
    case 'charge': {
      const out: LabelSlot[] = slot('label', a.label, a.color, around([a.x, a.y], CHARGE_R, a.label ?? '', [225, 315, 135, 45, 270, 90]))
      if (a.velocity_deg !== null) {
        const tip = polar(a.x, a.y, CHARGE_R + VEC_LEN, a.velocity_deg)
        out.push(...slot('v', 'v', 'blue', around(tip, 6, 'v', [a.velocity_deg, a.velocity_deg + 90, a.velocity_deg - 90])))
      }
      if (a.force_deg !== null) {
        const tip = polar(a.x, a.y, CHARGE_R + VEC_LEN, a.force_deg)
        out.push(...slot('F', 'F', 'red', around(tip, 6, 'F', [a.force_deg, a.force_deg + 90, a.force_deg - 90])))
      }
      return out
    }
    case 'compass':
      return slot('label', a.label, 'green', around([a.x, a.y], COMPASS_R, a.label ?? '', [270, 0, 180, 90]))
    case 'axes':
      return [
        ...slot('x', a.x_label, a.color, around([a.x + a.w, a.y], 8, a.x_label ?? '', [0, 270, 315, 45])),
        ...slot('y', a.y_label, a.color, around([a.x, a.y - a.h], 8, a.y_label ?? '', [90, 180, 135, 225, 315])),
      ]
    case 'region': {
      // Just outside the boundary
      const w = textWidth(a.curve_label ?? '', LABEL_PX)
      const cands = [45, 315, 135, 225, 0, 180].map((deg): Point => [
        a.cx + Math.cos(rad(deg)) * (a.w / 2 + 10 + w / 2),
        a.cy - Math.sin(rad(deg)) * (a.h / 2 + 10 + LABEL_PX / 2),
      ])
      return slot('curve', a.curve_label, a.color, cands)
    }
    case 'ring':
      return slot('label', a.label, a.color, around([a.cx, a.cy], a.r, a.label ?? '', [270, 0, 180, 90]))
    case 'body': {
      const out: LabelSlot[] = slot('label', a.label, a.color, around([a.x, a.y], BODY_R, a.label ?? '', [0, 180, 45, 135, 315, 225]))
      a.vectors.forEach((v, i) => {
        const tip = polar(a.x, a.y, BODY_R + VEC_LEN, v.deg)
        out.push(...slot(`v${i}`, v.label, v.color, around(tip, 6, v.label, [v.deg, v.deg + 90, v.deg - 90])))
      })
      return out
    }
    case 'ground':
      return slot('label', a.label, 'ink', around([a.x + a.w, a.y], 4, a.label ?? '', [0, 315, 45]))
    case 'curve': {
      const end = a.points[a.points.length - 1]
      return slot('label', a.label, a.color, around(end, 6, a.label ?? '', [0, 45, 315, 90, 270, 180]))
    }
    default:
      return []
  }
}

function labelMark(s: LabelSlot, pos: Point | undefined): Mark {
  const [x, y] = pos ?? s.cands[0]
  return { k: 'text', x, y: y + LABEL_PX * 0.35, text: s.text, px: LABEL_PX, weight: 400, color: s.color, anchor: 'middle', label: true }
}

/** The label mark a slot would get with its centre at `pos`. */
export const labelAt = (s: LabelSlot, pos: Point) => labelMark(s, pos)

// ---- marks per action ----

const CHARGE_R = 20
const VEC_LEN = 110
const COMPASS_R = 42
const BODY_R = 22

/** Marks of an action without its placeable labels. */
export function bodyMarks(a: DrawAction): Mark[] {
  switch (a.type) {
    case 'text':
      return [{ k: 'text', x: a.x, y: a.y, text: a.text, px: FONT_SIZE[a.size], weight: a.size === 'h1' ? 700 : 400, color: a.color, anchor: 'start' }]
    case 'line':
      return [stroke([a.start, a.end], a.color, 4, { dashed: a.dashed })]
    case 'arrow':
      return arrowMarks(a.start, a.end, a.color)
    case 'rect': {
      const { x, y, w, h } = a
      const marks: Mark[] = [stroke([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], a.color, 4, {}, true)]
      if (a.label) marks.push({ k: 'text', x: x + w / 2, y: y + h / 2 + 10, text: a.label, px: LABEL_PX, weight: 400, color: a.color, anchor: 'middle', label: true })
      return marks
    }
    case 'circle':
      return [ring(a.cx, a.cy, a.r, a.color, 4, a.filled ? a.color : undefined)]
    case 'angle': {
      const sweep = (((a.end_deg - a.start_deg) % 360) + 360) % 360
      const pts = ellipsePoints(a.cx, a.cy, a.r, a.r, a.start_deg, a.start_deg + sweep, 16)
      return [stroke(pts, a.color, 3)]
    }
    case 'field_mark': {
      const { x, y } = a
      const marks: Mark[] = [ring(x, y, 16, a.color, 3)]
      if (a.dir === 'out') marks.push(ring(x, y, 4, a.color, 1, a.color))
      else marks.push(stroke([[x - 10, y - 10], [x + 10, y + 10]], a.color, 3), stroke([[x + 10, y - 10], [x - 10, y + 10]], a.color, 3))
      return marks
    }
    case 'highlight':
      return []
    case 'wire': {
      const [s, e] = [a.start, a.end]
      const mid: Point = [(s[0] + e[0]) / 2, (s[1] + e[1]) / 2]
      const marks: Mark[] = [stroke([s, e], a.color, 7)]
      if (a.current) marks.push(chevron(mid, Math.atan2(e[1] - s[1], e[0] - s[0]), 'blue', 22))
      return marks
    }
    case 'charge': {
      const marks: Mark[] = [ring(a.x, a.y, CHARGE_R, a.color, 3, a.color, 0.15)]
      marks.push({ k: 'text', x: a.x, y: a.y + 10, text: a.sign === '-' ? '−' : '+', px: 30, weight: 700, color: a.color, anchor: 'middle' })
      for (const [deg, color] of [
        [a.velocity_deg, 'blue'],
        [a.force_deg, 'red'],
      ] as const) {
        if (deg === null) continue
        marks.push(...arrowMarks(polar(a.x, a.y, CHARGE_R + 4, deg), polar(a.x, a.y, CHARGE_R + VEC_LEN, deg), color))
      }
      return marks
    }
    case 'compass': {
      const { x, y } = a
      const n = polar(x, y, COMPASS_R - 8, a.needle_deg)
      const s = polar(x, y, COMPASS_R - 8, a.needle_deg + 180)
      const l = polar(x, y, 7, a.needle_deg + 90)
      const r = polar(x, y, 7, a.needle_deg - 90)
      return [
        ring(x, y, COMPASS_R, 'green', 4),
        stroke([l, n, r], 'red', 3, { fill: 'red', fillOpacity: 1 }, true),
        stroke([l, s, r], 'ink', 3, { fill: 'ink', fillOpacity: 0.15 }, true),
      ]
    }
    case 'magnet': {
      const { x, y, w, h } = a
      const vertical = a.north === 'top' || a.north === 'bottom'
      const halves: [Box, Box] = vertical
        ? [{ x, y, w, h: h / 2 }, { x, y: y + h / 2, w, h: h / 2 }]
        : [{ x, y, w: w / 2, h }, { x: x + w / 2, y, w: w / 2, h }]
      const northFirst = a.north === 'left' || a.north === 'top'
      return halves.flatMap((b, i) => {
        const north = (i === 0) === northFirst
        const color: Color = north ? 'red' : 'blue'
        return [
          stroke([[b.x, b.y], [b.x + b.w, b.y], [b.x + b.w, b.y + b.h], [b.x, b.y + b.h]], color, 4, { fill: color, fillOpacity: 0.12 }, true),
          { k: 'text', x: b.x + b.w / 2, y: b.y + b.h / 2 + 12, text: north ? 'N' : 'S', px: 34, weight: 700, color, anchor: 'middle' } as Mark,
        ]
      })
    }
    case 'axes':
      return [...arrowMarks([a.x, a.y], [a.x + a.w, a.y], a.color, 3, 18), ...arrowMarks([a.x, a.y], [a.x, a.y - a.h], a.color, 3, 18)]
    case 'region': {
      const { cx, cy, w, h } = a
      const pts: Point[] =
        a.shape === 'rect'
          ? [[cx - w / 2, cy - h / 2], [cx + w / 2, cy - h / 2], [cx + w / 2, cy + h / 2], [cx - w / 2, cy + h / 2]]
          : ellipsePoints(cx, cy, w / 2, h / 2).slice(0, -1)
      const outline = stroke(pts, a.color, 4, { fill: a.color, fillOpacity: 0.06 }, true)
      if (a.shape === 'ellipse' && outline.k === 'path') {
        const rx = w / 2
        const ry = h / 2
        outline.d = `M${cx + rx},${cy} A${rx},${ry} 0 1,0 ${cx - rx},${cy} A${rx},${ry} 0 1,0 ${cx + rx},${cy}`
      }
      const marks: Mark[] = [outline]
      if (a.orientation) marks.push(chevron([cx, cy - h / 2], a.orientation === 'ccw' ? Math.PI : 0, a.color, 22))
      if (a.label) marks.push({ k: 'text', x: cx, y: cy + 10, text: a.label, px: LABEL_PX + 4, weight: 400, color: a.color, anchor: 'middle' })
      return marks
    }
    case 'flow':
      return flowMarks(a)
    case 'chain':
      return chainMarks(a)
    case 'body': {
      const r = BODY_R
      const marks: Mark[] =
        a.shape === 'box'
          ? [stroke([[a.x - r, a.y - r], [a.x + r, a.y - r], [a.x + r, a.y + r], [a.x - r, a.y + r]], a.color, 4, { fill: a.color, fillOpacity: 0.12 }, true)]
          : [ring(a.x, a.y, r, a.color, 4, a.color, 0.12)]
      for (const v of a.vectors) marks.push(...arrowMarks(polar(a.x, a.y, r + 4, v.deg), polar(a.x, a.y, r + VEC_LEN, v.deg), v.color))
      return marks
    }
    case 'ground': {
      const marks: Mark[] = [stroke([[a.x, a.y], [a.x + a.w, a.y]], 'ink', 4)]
      // Hatching below the surface
      for (let x = a.x + 10; x < a.x + a.w; x += 24) marks.push(stroke([[x, a.y + 4], [x - 14, a.y + 20]], 'ink', 2))
      return marks
    }
    case 'curve':
      return [{ k: 'path', d: smoothPath(a.points), color: a.color, width: 4, dashed: a.dashed, segs: poly(a.points) }]
    case 'ring': {
      const pts = Array.from({ length: a.size }, (_, i) => polar(a.cx, a.cy, a.r, 90 + (360 * i) / a.size))
      const marks: Mark[] = [stroke(pts, a.color, 4, {}, true)]
      if (a.aromatic) marks.push(ring(a.cx, a.cy, a.r * 0.6, a.color, 3))
      return marks
    }
  }
}

/** A smooth path through the points (Catmull-Rom as cubic Béziers). */
function smoothPath(p: Point[]): string {
  if (p.length < 3) return pathOf(p)
  let d = `M${p[0][0]},${p[0][1]}`
  for (let i = 0; i < p.length - 1; i++) {
    const p0 = p[Math.max(0, i - 1)]
    const p1 = p[i]
    const p2 = p[i + 1]
    const p3 = p[Math.min(p.length - 1, i + 2)]
    const c1 = [p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6]
    const c2 = [p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6]
    d += ` C${c1[0].toFixed(1)},${c1[1].toFixed(1)} ${c2[0].toFixed(1)},${c2[1].toFixed(1)} ${p2[0]},${p2[1]}`
  }
  return d
}

/** Parallel strokes for a single, double or triple bond from a to b. */
function bondMarks(a: Point, b: Point, bond: Bond, color: Color): Mark[] {
  const len = Math.hypot(b[0] - a[0], b[1] - a[1]) || 1
  const nx = -(b[1] - a[1]) / len
  const ny = (b[0] - a[0]) / len
  const offsets = bond === '≡' ? [-7, 0, 7] : bond === '=' ? [-4.5, 4.5] : [0]
  return offsets.map((o) => stroke([[a[0] + nx * o, a[1] + ny * o], [b[0] + nx * o, b[1] + ny * o]], color, 3))
}

function chainMarks(a: Extract<DrawAction, { type: 'chain' }>): Mark[] {
  const px = FONT_SIZE.body
  const gap = 46
  const rowStep = 80
  const mid = px * 0.32 // height of a bond above the baseline
  const marks: Mark[] = []
  const centres: number[] = []
  let x = a.x
  a.groups.forEach((g, i) => {
    const w = textWidth(g, px)
    marks.push({ k: 'text', x, y: a.y, text: g, px, weight: 400, color: a.color, anchor: 'start' })
    centres.push(x + w / 2)
    if (i < a.groups.length - 1) marks.push(...bondMarks([x + w + 8, a.y - mid], [x + w + gap - 8, a.y - mid], a.bonds[i] ?? '-', a.color))
    x += w + gap
  })
  for (const br of a.branches) {
    const cx = centres[br.at]
    if (cx === undefined) continue
    const sign = br.dir === 'up' ? -1 : 1
    let y = a.y
    br.groups.forEach((g, i) => {
      const next = y + sign * rowStep
      // From just past this row's text to just short of the next row's
      const from = sign < 0 ? y - px * 0.8 - 6 : y + px * 0.25 + 6
      const to = sign < 0 ? next + px * 0.25 + 6 : next - px * 0.8 - 6
      marks.push(...bondMarks([cx, from], [cx, to], i === 0 ? br.bond : '-', a.color))
      marks.push({ k: 'text', x: cx, y: next, text: g, px, weight: 400, color: a.color, anchor: 'middle' })
      y = next
    })
  }
  return marks
}

function flowMarks(a: Extract<DrawAction, { type: 'flow' }>): Mark[] {
  const px = FONT_SIZE[a.size]
  const marks: Mark[] = []
  let x = a.x
  let y = a.y
  a.items.forEach((item, i) => {
    const w = textWidth(item, px)
    marks.push({ k: 'text', x, y, text: item, px, weight: 400, color: a.color, anchor: 'start' })
    if (i === a.items.length - 1) return
    if (a.dir === 'down') {
      const ax = x + Math.min(w, 120) / 2
      const top = y + px * 0.4
      const bottom = y + px * 0.4 + 56
      marks.push(...arrowMarks([ax, top], [ax, bottom], 'blue', 3, 16))
      if (a.connector) marks.push({ k: 'text', x: ax + 16, y: (top + bottom) / 2 + 9, text: a.connector, px: 24, weight: 400, color: 'blue', anchor: 'start' })
      y += px * 1.05 + 64
    } else {
      const gap = a.connector ? Math.max(90, textWidth(a.connector, 24) + 40) : 90
      const ay = y - px * 0.3
      marks.push(...arrowMarks([x + w + 14, ay], [x + w + gap - 14, ay], 'blue', 3, 16))
      if (a.connector) marks.push({ k: 'text', x: x + w + gap / 2, y: ay - 14, text: a.connector, px: 24, weight: 400, color: 'blue', anchor: 'middle' })
      x += w + gap
    }
  })
  return marks
}

/** Every mark of an action: body first, then its labels at the positions the layout chose. */
export function marksOf(a: DrawAction): Mark[] {
  return [...bodyMarks(a), ...labelSlots(a).map((s) => labelMark(s, a.labelPos?.[s.key]))]
}

// ---- boxes ----

export function segsBox(segs: Seg[]): Box {
  const xs = segs.flatMap(([p, q]) => [p[0], q[0]])
  const ys = segs.flatMap(([p, q]) => [p[1], q[1]])
  const x = Math.min(...xs)
  const y = Math.min(...ys)
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y }
}

export function markBox(m: Mark): Box {
  return m.k === 'text' ? textBox(m) : segsBox(m.segs)
}

export function union(boxes: Box[]): Box | null {
  if (!boxes.length) return null
  const x = Math.min(...boxes.map((b) => b.x))
  const y = Math.min(...boxes.map((b) => b.y))
  return { x, y, w: Math.max(...boxes.map((b) => b.x + b.w)) - x, h: Math.max(...boxes.map((b) => b.y + b.h)) - y }
}

/** Bounding box of everything an action draws (used by highlights and to keep items on the board). */
export function actionBox(a: DrawAction): Box | null {
  return union(marksOf(a).map(markBox))
}
