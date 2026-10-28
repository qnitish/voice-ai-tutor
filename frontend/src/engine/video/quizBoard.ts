/**
 * A checkpoint question written on the whiteboard, as the teacher would: a heading, the question, the options A–D
 * one below the other, then whatever follows the student's pick (a red cross, a green tick and box, a hint or the
 * explanation) further down. Laid out here from the stored question, so no LLM is involved and the board always
 * matches it; text is wrapped to the board's width with the board font.
 */
import { BOARD_W, type Box, FONT_SIZE, textWidth } from '@/engine/geometry'
import type { Color, DrawAction } from '@/lib/types'

const LEFT = 60
const RIGHT = BOARD_W - 60
const HEAD_Y = 80
const LINE = Math.round(FONT_SIZE.body * 1.35)
/** Space between the question and the options, between options, and before a note */
const GAP = { options: 26, option: 12, note: 34 }
const LETTERS = 'ABCD'
/** A question's picture: at most this big (board units) in a column on the right, beside the text */
const IMAGE_MAX = { w: 560, h: 520 }
const IMAGE_GAP = 40

/** `text` broken into lines no wider than `width` at `px` (a word longer than a line gets a line of its own). */
export function wrap(text: string, width: number, px: number = FONT_SIZE.body): string[] {
  const lines: string[] = []
  let line = ''
  for (const word of text.split(/\s+/).filter(Boolean)) {
    const next = line ? `${line} ${word}` : word
    if (line && textWidth(next, px) > width) {
      lines.push(line)
      line = word
    } else line = next
  }
  if (line) lines.push(line)
  return lines
}

const text = (id: string, x: number, y: number, t: string, color: Color = 'ink', size: 'h2' | 'body' = 'body'): DrawAction => ({
  type: 'text',
  id,
  x,
  y,
  text: t,
  size,
  color,
})

/** Box around baseline-placed body lines from `firstY` to `lastY`, `x` to `x + w` */
const linesBox = (x: number, firstY: number, lastY: number, w: number): Box => ({
  x,
  y: firstY - FONT_SIZE.body * 0.8,
  w,
  h: lastY - firstY + FONT_SIZE.body * 1.05,
})

export interface QuizLayout {
  /** "Question n of N" and the question's lines */
  question: DrawAction[]
  /** Each option's lines ("A. …", wrapped and indented) */
  options: DrawAction[][]
  /** Where each option's text sits (for its cross, tick and box) */
  boxes: Box[]
  /** Baseline of the last option line: notes go below it */
  bottom: number
}

export interface QuizImage {
  url: string
  /** Pixel size, for its aspect ratio */
  w: number
  h: number
}

/**
 * The question's board: heading, question and options in a column on the left, and its picture (if any) on the
 * right, scaled to fit beside them; with no picture the text takes the board's whole width.
 */
export function quizLayout(question: string, options: string[], n: number, total: number, image: QuizImage | null = null): QuizLayout {
  const out: QuizLayout = { question: [text('qz-head', LEFT, HEAD_Y, `Question ${n} of ${total}`, 'blue', 'h2')], options: [], boxes: [], bottom: 0 }
  let right = RIGHT
  let imageBottom = 0
  if (image && image.w > 0 && image.h > 0) {
    const scale = Math.min(IMAGE_MAX.w / image.w, IMAGE_MAX.h / image.h)
    const w = Math.round(image.w * scale)
    const h = Math.round(image.h * scale)
    const top = HEAD_Y + 30
    out.question.push({ type: 'image', id: 'qz-img', x: RIGHT - w, y: top, w, h, src: image.url })
    right = RIGHT - w - IMAGE_GAP
    imageBottom = top + h
  }
  let y = HEAD_Y + Math.round(FONT_SIZE.h2 * 1.6)
  wrap(question, right - LEFT).forEach((line, i) => {
    out.question.push(text(`qz-q${i}`, LEFT, y, line))
    y += LINE
  })
  y += GAP.options - LINE
  options.forEach((option, j) => {
    y += LINE
    const label = `${LETTERS[j]}. `
    const indent = textWidth(label, FONT_SIZE.body)
    const lines = wrap(option, right - LEFT - indent - 60) // room for the tick or cross after it
    const first = y
    const actions = lines.map((line, k) => {
      if (k) y += LINE
      return text(`qz-o${j}-${k}`, k ? LEFT + indent : LEFT, y, k ? line : label + line)
    })
    const w = Math.max(...lines.map((line, k) => (k ? indent : 0) + textWidth(k ? line : label + line, FONT_SIZE.body)))
    out.options.push(actions)
    out.boxes.push(linesBox(LEFT, first, y, w))
    y += GAP.option
  })
  // Notes go below both the options and the picture
  out.bottom = Math.max(y - GAP.option, imageBottom - FONT_SIZE.body * 0.25)
  return out
}

/** A red strike through a wrong pick and a cross after it. */
export function markWrong(box: Box, j: number): DrawAction[] {
  const lines = Math.round((box.h - FONT_SIZE.body * 1.05) / LINE) + 1
  const strikes: DrawAction[] = Array.from({ length: lines }, (_, k) => {
    const y = box.y + FONT_SIZE.body * 0.45 + k * LINE
    return { type: 'line', id: `qz-x${j}-${k}`, start: [box.x, y], end: [box.x + box.w, y], color: 'red', dashed: false }
  })
  return [...strikes, text(`qz-xm${j}`, box.x + box.w + 20, box.y + FONT_SIZE.body * 0.8, '✗', 'red')]
}

/** A green box around the right option and a tick after it. */
export function markRight(box: Box, j: number): DrawAction[] {
  const pad = 10
  return [
    { type: 'rect', id: `qz-ok${j}`, x: box.x - pad, y: box.y - pad, w: box.w + 2 * pad, h: box.h + 2 * pad, color: 'green', label: null },
    text(`qz-okm${j}`, box.x + box.w + 24, box.y + FONT_SIZE.body * 0.8, '✓', 'green'),
  ]
}

/** A note (hint, explanation) written below `below`; returns its lines and the baseline of its last line. */
export function note(id: string, body: string, below: number, color: Color): { actions: DrawAction[]; bottom: number } {
  let y = below + GAP.note + LINE - FONT_SIZE.body * 0.4
  const actions = wrap(body, RIGHT - LEFT).map((line, i) => {
    if (i) y += LINE
    return text(`${id}-${i}`, LEFT, y, line, color)
  })
  return { actions, bottom: y }
}
