/**
 * Keeps board items from overlapping. The LLM places items by coordinates and often gets it wrong, so before a
 * step is drawn each new item is checked against the board:
 * - text that overlaps anything moves to the next free line (and is pulled inside the board),
 * - a shape whose strokes cross text, or whose own text is crossed, shifts by the smallest free offset
 *   (shapes may overlap each other: diagrams touch on purpose),
 * - labels take the first free spot of their candidates,
 * - a motion's trail and its items' end positions count as taken, so later items keep clear of them.
 * The result only depends on the input, so a resumed board is laid out exactly as it was first drawn.
 */
import type { DrawAction, Point } from '@/lib/types'
import {
  BOARD_H,
  BOARD_W,
  type Box,
  bodyMarks,
  FONT_SIZE,
  labelAt,
  labelSlots,
  type Mark,
  markBox,
  marksOf,
  type Seg,
  textBox,
  textWidth,
  union,
} from '@/engine/geometry'
import { motionFrame } from '@/engine/motion'

const MARGIN = 16
const PAD = 6

interface Placed {
  texts: Box[]
  segs: Seg[]
  fills: Box[]
}

function add(placed: Placed, marks: Mark[]) {
  for (const m of marks) {
    if (m.k === 'text') placed.texts.push(textBox(m))
    else if (m.k === 'image') placed.fills.push(markBox(m)) // text keeps off a picture
    else {
      placed.segs.push(...m.segs)
      if (m.fill) placed.fills.push(markBox(m))
    }
  }
}

const boxesHit = (a: Box, b: Box, pad = PAD) =>
  a.x < b.x + b.w + pad && b.x < a.x + a.w + pad && a.y < b.y + b.h + pad && b.y < a.y + a.h + pad

function segHitsBox([p, q]: Seg, b: Box, pad = PAD): boolean {
  const x0 = b.x - pad
  const y0 = b.y - pad
  const x1 = b.x + b.w + pad
  const y1 = b.y + b.h + pad
  // Liang–Barsky clip: does the segment pass through the box?
  let t0 = 0
  let t1 = 1
  const dx = q[0] - p[0]
  const dy = q[1] - p[1]
  for (const [pp, qq] of [
    [-dx, p[0] - x0],
    [dx, x1 - p[0]],
    [-dy, p[1] - y0],
    [dy, y1 - p[1]],
  ]) {
    if (pp === 0) {
      if (qq < 0) return false
      continue
    }
    const t = qq / pp
    if (pp < 0) t0 = Math.max(t0, t)
    else t1 = Math.min(t1, t)
    if (t0 > t1) return false
  }
  return true
}

// Diagrams stay below the title row, so a tall diagram can't push the title away
const TITLE_BAND = 112

const inside = (b: Box, top = MARGIN) => b.x >= MARGIN && b.y >= top && b.x + b.w <= BOARD_W - MARGIN && b.y + b.h <= BOARD_H - MARGIN

/** Text box overlaps anything on the board. */
const textBlocked = (b: Box, placed: Placed) =>
  placed.texts.some((t) => boxesHit(b, t)) || placed.fills.some((f) => boxesHit(b, f, 0)) || placed.segs.some((s) => segHitsBox(s, b))

/** A shape's marks cross text on the board, or the board's strokes cross the shape's text. */
function shapeBlocked(marks: Mark[], placed: Placed): boolean {
  for (const m of marks) {
    if (m.k === 'text') {
      if (textBlocked(textBox(m), placed)) return true
    } else if (m.k === 'image') {
      if (placed.texts.some((t) => boxesHit(markBox(m), t))) return true
    } else if (m.segs.some((s) => placed.texts.some((t) => segHitsBox(s, t, 10)))) return true
  }
  return false
}

/** Move every coordinate of an action by (dx, dy). */
export function translate(a: DrawAction, dx: number, dy: number): DrawAction {
  const out = { ...a } as Record<string, unknown>
  const mv = (p: Point): Point => [p[0] + dx, p[1] + dy]
  for (const [kx, ky] of [
    ['x', 'y'],
    ['cx', 'cy'],
  ]) {
    if (typeof out[kx] === 'number') {
      out[kx] = (out[kx] as number) + dx
      out[ky] = (out[ky] as number) + dy
    }
  }
  if (Array.isArray(out.start)) out.start = mv(out.start as Point)
  if (Array.isArray(out.end)) out.end = mv(out.end as Point)
  if (Array.isArray(out.points)) out.points = (out.points as Point[]).map(mv)
  delete out.labelPos
  return out as unknown as DrawAction
}

/** Shift needed to bring a box inside the board (0 when it fits or can't fit). */
function intoBoard(b: Box, top = MARGIN): [number, number] {
  const shift = (lo: number, size: number, min: number, max: number) => {
    if (size > max - min) return 0
    if (lo < min) return min - lo
    if (lo + size > max) return max - (lo + size)
    return 0
  }
  return [shift(b.x, b.w, MARGIN, BOARD_W - MARGIN), shift(b.y, b.h, top, BOARD_H - MARGIN)]
}

const SIZES = ['h1', 'h2', 'body', 'small'] as const

function placeText(a: Extract<DrawAction, { type: 'text' }>, placed: Placed): DrawAction {
  let t = { ...a }
  // Too wide for the board: a smaller size
  while (textWidth(t.text, FONT_SIZE[t.size], t.size === 'h1' ? 700 : 400) > BOARD_W - 2 * MARGIN && t.size !== 'small') {
    t = { ...t, size: SIZES[SIZES.indexOf(t.size) + 1] }
  }
  const box = () => markBox(bodyMarks(t)[0])
  const [dx, dy] = intoBoard(box())
  t = { ...t, x: t.x + dx, y: t.y + dy }
  if (!textBlocked(box(), placed)) return t
  // Next free line below, then above
  const step = Math.round(FONT_SIZE[t.size] * 1.3)
  for (const dir of [1, -1]) {
    for (let k = 1; k <= 12; k++) {
      const c = { ...t, y: t.y + dir * k * step }
      const b = markBox(bodyMarks(c)[0])
      if (!inside(b)) break
      if (!textBlocked(b, placed)) return c
    }
  }
  return t
}

// Offsets tried for a blocked shape, nearest first
const OFFSETS: [number, number][] = (() => {
  const out: [number, number][] = []
  for (let r = 20; r <= 260; r += 20) {
    for (let i = 0; i < 16; i++) {
      const ang = (i * Math.PI) / 8
      out.push([Math.round(r * Math.cos(ang)), Math.round(r * Math.sin(ang))])
    }
  }
  return out
})()

function placeShape(a: DrawAction, placed: Placed): DrawAction {
  const fits = (c: DrawAction) => {
    const marks = bodyMarks(c)
    const b = union(marks.map(markBox))
    return (!b || inside(b, TITLE_BAND)) && !shapeBlocked(marks, placed)
  }
  let s = a
  const b = union(bodyMarks(s).map(markBox))
  if (b) {
    const [dx, dy] = intoBoard(b, TITLE_BAND)
    if (dx || dy) s = translate(s, dx, dy)
  }
  if (fits(s)) return s
  for (const [dx, dy] of OFFSETS) {
    const c = translate(s, dx, dy)
    if (fits(c)) return c
  }
  return s
}

/** Number of things a box overlaps (to pick the least bad label spot when none is free). */
function hitCount(b: Box, placed: Placed): number {
  return (
    placed.texts.filter((t) => boxesHit(b, t)).length +
    placed.fills.filter((f) => boxesHit(b, f, 0)).length +
    placed.segs.filter((s) => segHitsBox(s, b)).length
  )
}

function placeLabels(a: DrawAction, placed: Placed): DrawAction {
  const slots = labelSlots(a)
  if (!slots.length) return a
  const labelPos: Record<string, Point> = {}
  for (const slot of slots) {
    const boxAt = (c: Point) => textBox(labelAt(slot, c) as Extract<Mark, { k: 'text' }>)
    const fits = slot.cands.filter((c) => inside(boxAt(c), TITLE_BAND))
    const free = fits.find((c) => !textBlocked(boxAt(c), placed))
    let pos = free
    if (!pos) {
      const scored = (fits.length ? fits : slot.cands).map((c) => [hitCount(boxAt(c), placed), c] as const)
      pos = scored.reduce((best, cur) => (cur[0] < best[0] ? cur : best))[1]
    }
    labelPos[slot.key] = pos
    add(placed, [labelAt(slot, pos)])
  }
  return { ...a, labelPos }
}

function placedFrom(board: DrawAction[]): Placed {
  const placed: Placed = { texts: [], segs: [], fills: [] }
  for (const a of board) add(placed, marksOf(a))
  return placed
}

/** Take the space a motion uses: its trail and ghosts, and its items where they end up. */
function addMotion(m: Extract<DrawAction, { type: 'motion' }>, known: DrawAction[], placed: Placed) {
  const frame = motionFrame([...known, m].map((action) => ({ action, p: 1, live: false })))
  add(placed, frame.under.get(m.id) ?? [])
  for (const tr of m.tracks) {
    const target = known.find((a) => a.id === tr.target)
    const [dx, dy] = frame.offsets.get(tr.target) ?? [0, 0]
    if (target && (dx || dy)) add(placed, bodyMarks(translate(target, dx, dy)))
  }
}

/** `known` = everything placed so far on this board, in order (motions look up their items in it). */
function placeBodies(draw: DrawAction[], placed: Placed, known: DrawAction[]): DrawAction[] {
  return draw.map((a) => {
    if (a.type === 'highlight' || a.type === 'motion') {
      if (a.type === 'motion') addMotion(a, known, placed)
      known.push(a)
      return a
    }
    const moved = a.type === 'text' ? placeText(a, placed) : placeShape(a, placed)
    add(placed, bodyMarks(moved))
    known.push(moved)
    return moved
  })
}

/**
 * Lay out one step's new actions against what is already on the board (for answers, which stream in).
 * Labels go last, so they avoid every new shape of the step.
 */
export function layoutStep(draw: DrawAction[], board: DrawAction[] = []): DrawAction[] {
  const placed = placedFrom(board)
  return placeBodies(draw, placed, [...board]).map((a) => placeLabels(a, placed))
}

/**
 * Lay out a whole scene: text and shapes step by step, then the labels, which then also avoid
 * shapes that later steps draw. Playing or resuming a scene uses the same result.
 */
export function layoutScene(steps: DrawAction[][]): DrawAction[][] {
  const placed: Placed = { texts: [], segs: [], fills: [] }
  const known: DrawAction[] = []
  const bodies = steps.map((draw) => placeBodies(draw, placed, known))
  return bodies.map((draw) => draw.map((a) => placeLabels(a, placed)))
}
