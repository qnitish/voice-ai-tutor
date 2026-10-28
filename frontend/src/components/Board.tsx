import type { ReactNode } from 'react'

import { BOARD_BG, BOARD_COLORS as COLORS, clamp01, type FrameItem, frameOf, GRID } from '@/engine/frame'
import { BOARD_H, BOARD_W, FONT_STACK, type Mark, textBox } from '@/engine/geometry'
import { usePlayer } from '@/store/player'

/** One mark at progress p (0 -> 1): strokes draw themselves, text is written left to right, labels fade in. */
function renderMark(m: Mark, p: number, key: string): ReactNode {
  // A picture fades in
  if (m.k === 'image') {
    return <image key={key} href={m.href} x={m.x} y={m.y} width={m.w} height={m.h} opacity={p} preserveAspectRatio="xMidYMid meet" />
  }
  if (m.k === 'path') {
    const common = { d: m.d, stroke: COLORS[m.color], strokeWidth: m.width, strokeLinecap: 'round', strokeLinejoin: 'round' } as const
    return (
      <g key={key} opacity={m.opacity}>
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

function renderItem(item: FrameItem): ReactNode {
  if (item.k === 'box') {
    const pad = 10
    const { box } = item
    return (
      <rect
        key={item.key}
        x={box.x - pad}
        y={box.y - pad}
        width={(box.w + pad * 2) * item.p}
        height={box.h + pad * 2}
        rx={8}
        fill={COLORS[item.color] ?? COLORS.orange}
        opacity={0.25}
      />
    )
  }
  const move = item.dx || item.dy ? `translate(${item.dx} ${item.dy})` : undefined
  if (item.k === 'glow') {
    // A shape is traced over with a highlighter instead of being boxed in
    const m = item.mark
    return (
      <g key={item.key} opacity={0.35} transform={move}>
        <path d={m.d} fill="none" stroke={COLORS[item.color] ?? COLORS.orange} strokeWidth={m.width + 12} strokeLinecap="round" strokeLinejoin="round" pathLength={1} strokeDasharray="1 1" strokeDashoffset={1 - item.p} />
      </g>
    )
  }
  const mark = renderMark(item.mark, item.p, item.key)
  return move ? <g key={item.key} transform={move}>{mark}</g> : mark
}

export function Board() {
  const drawn = usePlayer((s) => s.drawn)
  const active = usePlayer((s) => s.active)
  const clipTime = usePlayer((s) => s.clipTime)
  const layer = usePlayer((s) => s.layer)
  const scroll = usePlayer((s) => s.boardScroll)

  return (
    <svg
      viewBox={`0 0 ${BOARD_W} ${BOARD_H}`}
      className="block h-full w-full"
      style={{ fontFamily: FONT_STACK }}
      role="img"
      aria-label="Whiteboard"
    >
      <defs>
        {/* The grid moves with the board when it scrolls */}
        <pattern id="grid" y={-scroll} width={GRID.step} height={GRID.step} patternUnits="userSpaceOnUse">
          <path d={`M${GRID.step} 0H0V${GRID.step}`} fill="none" stroke={GRID.color} strokeWidth="1" />
        </pattern>
      </defs>
      <rect width={BOARD_W} height={BOARD_H} fill={BOARD_BG[layer]} />
      <rect width={BOARD_W} height={BOARD_H} fill="url(#grid)" opacity={GRID.opacity} />
      <g transform={scroll ? `translate(0 ${-scroll})` : undefined}>{frameOf(drawn, active, clipTime).map(renderItem)}</g>
    </svg>
  )
}
