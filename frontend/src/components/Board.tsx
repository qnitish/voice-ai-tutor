import type { ReactNode } from 'react'

import type { Color, DrawAction, Point } from '@/lib/types'
import { type TimedAction, usePlayer } from '@/store/player'

export const BOARD_W = 1600
export const BOARD_H = 900

const COLORS: Record<Color, string> = {
  ink: '#1f2937',
  blue: '#2563eb',
  red: '#dc2626',
  green: '#15803d',
  orange: '#f59e0b',
}
const FONT_SIZE = { h1: 52, h2: 38, body: 30, small: 22 }
// Rough average glyph width of the handwriting font, as a fraction of font size
const CHAR_W = 0.5

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

function textWidth(text: string, size: keyof typeof FONT_SIZE) {
  return text.length * FONT_SIZE[size] * CHAR_W
}

type Box = { x: number; y: number; w: number; h: number }

/** Approximate bounding box, used by highlight actions. */
function bbox(a: DrawAction): Box | null {
  switch (a.type) {
    case 'text': {
      const fs = FONT_SIZE[a.size]
      return { x: a.x, y: a.y - fs * 0.85, w: textWidth(a.text, a.size), h: fs * 1.1 }
    }
    case 'line':
    case 'arrow': {
      const [x1, y1] = a.start
      const [x2, y2] = a.end
      return { x: Math.min(x1, x2), y: Math.min(y1, y2), w: Math.abs(x2 - x1), h: Math.abs(y2 - y1) }
    }
    case 'rect':
      return { x: a.x, y: a.y, w: a.w, h: a.h }
    case 'circle':
    case 'angle':
      return { x: a.cx - a.r, y: a.cy - a.r, w: a.r * 2, h: a.r * 2 }
    case 'field_mark':
      return { x: a.x - 18, y: a.y - 18, w: 36, h: 36 }
    case 'highlight':
      return null
  }
}

/** A stroke that draws itself as progress goes 0 -> 1. */
function Stroke({ d, p, color, width = 4, dashed = false }: { d: string; p: number; color: string; width?: number; dashed?: boolean }) {
  const common = { d, fill: 'none', stroke: color, strokeWidth: width, strokeLinecap: 'round', strokeLinejoin: 'round' } as const
  // A dashed line can't use the dash-offset reveal, so it fades in instead
  if (dashed) return <path {...common} strokeDasharray="14 12" opacity={p} />
  return <path {...common} pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - p} />
}

function Label({ x, y, text, color, p, anchor = 'start' }: { x: number; y: number; text: string; color: string; p: number; anchor?: 'start' | 'middle' }) {
  return (
    <text x={x} y={y} fill={color} fontSize={28} textAnchor={anchor} opacity={clamp01((p - 0.7) / 0.3)}>
      {text}
    </text>
  )
}

const polar = (cx: number, cy: number, r: number, deg: number): Point => [
  cx + r * Math.cos((deg * Math.PI) / 180),
  cy - r * Math.sin((deg * Math.PI) / 180),
]

function renderAction(a: DrawAction, p: number, all: DrawAction[]): ReactNode {
  const color = COLORS[a.color] ?? COLORS.ink
  switch (a.type) {
    case 'text': {
      const w = textWidth(a.text, a.size) * 1.4 + 20
      const fs = FONT_SIZE[a.size]
      const clipId = `clip-${a.id}`
      return (
        <g key={a.id}>
          {p < 1 && (
            <clipPath id={clipId}>
              <rect x={a.x - 4} y={a.y - fs * 1.2} width={w * p} height={fs * 1.7} />
            </clipPath>
          )}
          <text
            x={a.x}
            y={a.y}
            fill={color}
            fontSize={fs}
            fontWeight={a.size === 'h1' ? 700 : 400}
            clipPath={p < 1 ? `url(#${clipId})` : undefined}
          >
            {a.text}
          </text>
        </g>
      )
    }
    case 'line': {
      const [x1, y1] = a.start
      const [x2, y2] = a.end
      return (
        <g key={a.id}>
          <Stroke d={`M${x1},${y1} L${x2},${y2}`} p={p} color={color} dashed={a.dashed} />
        </g>
      )
    }
    case 'arrow': {
      const [x1, y1] = a.start
      const [x2, y2] = a.end
      const ang = Math.atan2(y2 - y1, x2 - x1)
      const head = 22
      const h1: Point = [x2 - head * Math.cos(ang - 0.45), y2 - head * Math.sin(ang - 0.45)]
      const h2: Point = [x2 - head * Math.cos(ang + 0.45), y2 - head * Math.sin(ang + 0.45)]
      const mx = (x1 + x2) / 2 - 20 * Math.sin(ang)
      const my = (y1 + y2) / 2 + 20 * Math.cos(ang)
      return (
        <g key={a.id}>
          <Stroke d={`M${x1},${y1} L${x2},${y2}`} p={clamp01(p / 0.85)} color={color} />
          <Stroke d={`M${h1[0]},${h1[1]} L${x2},${y2} L${h2[0]},${h2[1]}`} p={clamp01((p - 0.85) / 0.15)} color={color} />
          {a.label && <Label x={mx + 8} y={my + 8} text={a.label} color={color} p={p} />}
        </g>
      )
    }
    case 'rect': {
      const { x, y, w, h } = a
      return (
        <g key={a.id}>
          <Stroke d={`M${x},${y} h${w} v${h} h${-w} Z`} p={p} color={color} />
          {a.label && <Label x={x + w / 2} y={y + h / 2 + 10} text={a.label} color={color} p={p} anchor="middle" />}
        </g>
      )
    }
    case 'circle': {
      const { cx, cy, r } = a
      const d = `M${cx + r},${cy} A${r},${r} 0 1,0 ${cx - r},${cy} A${r},${r} 0 1,0 ${cx + r},${cy}`
      return (
        <g key={a.id}>
          {a.filled && <circle cx={cx} cy={cy} r={r} fill={color} opacity={p} />}
          <Stroke d={d} p={p} color={color} />
          {a.label && <Label x={cx + r + 10} y={cy + 10} text={a.label} color={color} p={p} />}
        </g>
      )
    }
    case 'angle': {
      const [sx, sy] = polar(a.cx, a.cy, a.r, a.start_deg)
      const [ex, ey] = polar(a.cx, a.cy, a.r, a.end_deg)
      const sweep = (((a.end_deg - a.start_deg) % 360) + 360) % 360
      const [lx, ly] = polar(a.cx, a.cy, a.r + 24, a.start_deg + sweep / 2)
      return (
        <g key={a.id}>
          <Stroke d={`M${sx},${sy} A${a.r},${a.r} 0 ${sweep > 180 ? 1 : 0},0 ${ex},${ey}`} p={p} color={color} width={3} />
          {a.label && <Label x={lx} y={ly + 10} text={a.label} color={color} p={p} anchor="middle" />}
        </g>
      )
    }
    case 'field_mark': {
      const { x, y } = a
      const r = 16
      const circle = `M${x + r},${y} A${r},${r} 0 1,0 ${x - r},${y} A${r},${r} 0 1,0 ${x + r},${y}`
      const inner = clamp01((p - 0.6) / 0.4)
      return (
        <g key={a.id}>
          <Stroke d={circle} p={clamp01(p / 0.6)} color={color} width={3} />
          {a.dir === 'out' ? (
            <circle cx={x} cy={y} r={4} fill={color} opacity={inner} />
          ) : (
            <Stroke d={`M${x - 10},${y - 10} L${x + 10},${y + 10} M${x + 10},${y - 10} L${x - 10},${y + 10}`} p={inner} color={color} width={3} />
          )}
        </g>
      )
    }
    case 'highlight': {
      const target = all.find((t) => t.id === a.target)
      const box = target && bbox(target)
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
  }
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
      style={{ fontFamily: "'Kalam', 'Geist Variable', sans-serif" }}
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
