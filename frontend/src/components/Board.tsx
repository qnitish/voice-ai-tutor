import type { ReactNode } from 'react'

import { actionBox, BOARD_H, BOARD_W, FONT_STACK, type Mark, marksOf, textBox } from '@/engine/geometry'
import type { Color, DrawAction } from '@/lib/types'
import { type TimedAction, usePlayer } from '@/store/player'

const COLORS: Record<Color, string> = {
  ink: '#1f2937',
  blue: '#2563eb',
  red: '#dc2626',
  green: '#15803d',
  orange: '#f59e0b',
}

// Highlights box these in; other actions get their strokes traced
const TEXT_LIKE = new Set<DrawAction['type']>(['text', 'flow', 'chain'])

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/** One mark at progress p (0 -> 1): strokes draw themselves, text is written left to right, labels fade in. */
function renderMark(m: Mark, p: number, key: string): ReactNode {
  if (m.k === 'path') {
    const common = { d: m.d, stroke: COLORS[m.color], strokeWidth: m.width, strokeLinecap: 'round', strokeLinejoin: 'round' } as const
    return (
      <g key={key}>
        {m.fill && <path d={m.d} fill={COLORS[m.fill]} opacity={(m.fillOpacity ?? 1) * clamp01((p - 0.6) / 0.4)} />}
        {/* A dashed line can't use the dash-offset reveal, so it fades in instead */}
        {m.dashed ? (
          <path {...common} fill="none" strokeDasharray="14 12" opacity={p} />
        ) : (
          <path {...common} fill="none" pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - p} />
        )}
      </g>
    )
  }
  const text = (
    <text x={m.x} y={m.y} fill={COLORS[m.color]} fontSize={m.px} fontWeight={m.weight} textAnchor={m.anchor}>
      {m.text}
    </text>
  )
  if (m.label) return <g key={key} opacity={clamp01((p - 0.5) / 0.5)}>{text}</g>
  if (p >= 1) return <g key={key}>{text}</g>
  const b = textBox(m)
  const clipId = `clip-${key}`
  return (
    <g key={key}>
      <clipPath id={clipId}>
        <rect x={b.x - 4} y={b.y - m.px * 0.3} width={(b.w + 8) * p} height={m.px * 1.7} />
      </clipPath>
      <g clipPath={`url(#${clipId})`}>{text}</g>
    </g>
  )
}

function renderAction(a: DrawAction, p: number, all: DrawAction[]): ReactNode {
  if (a.type === 'highlight') {
    const target = all.find((t) => t.id === a.target)
    if (!target) return null
    const color = COLORS[a.color] ?? COLORS.orange
    if (!TEXT_LIKE.has(target.type)) {
      // A shape is traced over with a highlighter instead of being boxed in
      return (
        <g key={a.id} opacity={0.35}>
          {marksOf(target).flatMap((m, i) =>
            m.k === 'path' ? [<path key={i} d={m.d} fill="none" stroke={color} strokeWidth={m.width + 12} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - p} />] : [],
          )}
        </g>
      )
    }
    const box = actionBox(target)
    if (!box) return null
    const pad = 10
    return (
      <rect
        key={a.id}
        x={box.x - pad}
        y={box.y - pad}
        width={(box.w + pad * 2) * p}
        height={box.h + pad * 2}
        rx={8}
        fill={color}
        opacity={0.25}
      />
    )
  }
  // A block's marks are drawn one after another within the action's time
  const marks = marksOf(a)
  const n = marks.length
  return <g key={a.id}>{marks.map((m, i) => renderMark(m, clamp01(p * n - i), `${a.id}-${i}`))}</g>
}

function progress(t: TimedAction, clipTime: number) {
  return clamp01((clipTime - t.start) / Math.max(1, t.end - t.start))
}

export function Board() {
  const drawn = usePlayer((s) => s.drawn)
  const active = usePlayer((s) => s.active)
  const clipTime = usePlayer((s) => s.clipTime)
  const layer = usePlayer((s) => s.layer)

  const visible = [...drawn, ...active.filter((t) => clipTime >= t.start).map((t) => t.action)]
  // Highlights render underneath so they don't cover the ink
  const order = (a: DrawAction) => (a.type === 'highlight' ? 0 : 1)
  const items = [
    ...drawn.map((a) => ({ a, p: 1 })),
    ...active.filter((t) => clipTime >= t.start).map((t) => ({ a: t.action, p: progress(t, clipTime) })),
  ].sort((x, y) => order(x.a) - order(y.a))

  return (
    <svg
      viewBox={`0 0 ${BOARD_W} ${BOARD_H}`}
      className="block h-full w-full"
      style={{ fontFamily: FONT_STACK }}
      role="img"
      aria-label="Whiteboard"
    >
      <defs>
        <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
          <path d="M40 0H0V40" fill="none" stroke="#e5e7eb" strokeWidth="1" />
        </pattern>
      </defs>
      <rect width={BOARD_W} height={BOARD_H} fill={layer === 'doubt' ? '#fffbeb' : '#ffffff'} />
      <rect width={BOARD_W} height={BOARD_H} fill="url(#grid)" opacity={0.6} />
      {items.map(({ a, p }) => renderAction(a, p, visible))}
    </svg>
  )
}
