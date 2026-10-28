import { type KeyboardEvent, type PointerEvent, useRef } from 'react'

import { mmss } from '@/components/video/format'
import type { Chapter, Checkpoint } from '@/lib/types'
import { cn } from '@/lib/utils'

const STEP_MS = 5000

/**
 * Seek bar of the lecture: progress, a tick where each chapter starts and a diamond per checkpoint (green once
 * done). A click or the arrow keys call `onSeek`; the engine clamps a forward seek at the next open checkpoint.
 */
export function SeekBar({
  positionMs,
  durationMs,
  chapters,
  checkpoints,
  done,
  disabled,
  onSeek,
}: {
  positionMs: number
  durationMs: number
  chapters: Chapter[]
  checkpoints: Checkpoint[]
  done: Set<number>
  disabled: boolean
  onSeek: (ms: number) => void
}) {
  const bar = useRef<HTMLDivElement>(null)
  const pct = (ms: number) => (durationMs ? `${Math.min(100, (100 * ms) / durationMs)}%` : '0%')

  function seekAt(e: PointerEvent<HTMLDivElement>) {
    const el = bar.current
    if (!el || disabled || !durationMs) return
    const r = el.getBoundingClientRect()
    const ratio = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width))
    onSeek(ratio * durationMs)
  }

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (disabled) return
    if (e.key === 'ArrowLeft') onSeek(Math.max(0, positionMs - STEP_MS))
    else if (e.key === 'ArrowRight') onSeek(Math.min(durationMs, positionMs + STEP_MS))
    else return
    e.preventDefault()
  }

  return (
    <div className="text-muted-foreground flex items-center gap-3 text-xs tabular-nums">
      <span className="w-10 text-right">{mmss(positionMs)}</span>
      <div
        ref={bar}
        role="slider"
        aria-label="Lecture position"
        aria-valuemin={0}
        aria-valuemax={durationMs}
        aria-valuenow={Math.round(positionMs)}
        aria-valuetext={mmss(positionMs)}
        aria-disabled={disabled}
        tabIndex={disabled ? -1 : 0}
        onPointerDown={seekAt}
        onKeyDown={onKey}
        className={cn(
          'focus-visible:ring-ring/50 relative h-6 flex-1 cursor-pointer touch-none rounded-sm select-none focus-visible:ring-[3px] focus-visible:outline-none',
          disabled && 'cursor-default opacity-60',
        )}
      >
        <div className="bg-muted absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 overflow-hidden rounded-full">
          <div className="bg-primary h-full rounded-full" style={{ width: pct(positionMs) }} />
        </div>
        {chapters
          .filter((c) => c.start_ms > 0)
          .map((c) => (
            <span
              key={c.idx}
              title={c.title}
              className="bg-foreground/40 absolute top-1/2 h-3 w-px -translate-y-1/2"
              style={{ left: pct(c.start_ms) }}
            />
          ))}
        {checkpoints.map((cp) => (
          <span
            key={cp.idx}
            title={`Checkpoint ${cp.idx + 1}: ${cp.questions.length} question${cp.questions.length === 1 ? '' : 's'}`}
            className={cn(
              'absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rotate-45 rounded-[2px] border',
              done.has(cp.idx) ? 'border-green-700 bg-green-500' : 'border-amber-600 bg-amber-400',
            )}
            style={{ left: pct(cp.at_ms) }}
          />
        ))}
        <span
          className="border-primary bg-background absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 shadow"
          style={{ left: pct(positionMs) }}
        />
      </div>
      <span className="w-10">{mmss(durationMs)}</span>
    </div>
  )
}
