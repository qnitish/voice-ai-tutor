import { mmss } from '@/components/video/format'
import type { Chapter } from '@/lib/types'
import { cn } from '@/lib/utils'

/** The lecture's chapters with start times, grouped by topic; a click seeks there (while playing or paused). */
export function ChapterList({
  chapters,
  currentIdx,
  disabled,
  onSelect,
}: {
  chapters: Chapter[]
  currentIdx: number
  disabled: boolean
  onSelect: (startMs: number) => void
}) {
  if (!chapters.length) return null
  return (
    <ol className="flex flex-col gap-0.5">
      {chapters.map((c, i) => (
        <li key={c.idx} className="flex flex-col">
          {c.topic && c.topic !== chapters[i - 1]?.topic && (
            <p className="text-muted-foreground mt-2 mb-1 text-xs font-medium tracking-wide uppercase first:mt-0">{c.topic}</p>
          )}
          <button
            type="button"
            disabled={disabled}
            aria-current={c.idx === currentIdx ? 'step' : undefined}
            onClick={() => onSelect(c.start_ms)}
            className={cn(
              'flex w-full items-baseline gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors enabled:hover:bg-muted disabled:cursor-default',
              c.idx === currentIdx && 'bg-muted font-medium',
            )}
          >
            <span className="text-muted-foreground w-10 shrink-0 text-xs tabular-nums">{mmss(c.start_ms)}</span>
            <span className="min-w-0 flex-1 leading-snug">{c.title}</span>
          </button>
        </li>
      ))}
    </ol>
  )
}
