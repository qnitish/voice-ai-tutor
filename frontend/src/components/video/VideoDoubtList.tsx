import { useEffect, useRef } from 'react'

import { Badge } from '@/components/ui/badge'
import { mmss } from '@/components/video/format'
import { type LineKey, t } from '@/lib/i18n'
import type { Lesson } from '@/lib/types'
import { cn } from '@/lib/utils'
import { useVideo, type VideoDoubtView } from '@/store/video'

const status = (lesson: Lesson | null, s: VideoDoubtView['status']) => (s === 'dismissed' ? '—' : t(lesson, s satisfies LineKey))

/** Scroll the list (only the list, never the page) so `item` is in view. */
function reveal(list: HTMLElement, item: HTMLElement) {
  const top = item.offsetTop
  const bottom = top + item.offsetHeight
  if (top < list.scrollTop) list.scrollTop = top
  else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight
}

/**
 * The session's doubts (this and earlier visits), with when in the lecture each was asked. The list scrolls inside
 * its own box (`className` sets the height), and follows a doubt that was just added or changed status.
 */
export function VideoDoubtList({ className }: { className?: string }) {
  const doubts = useVideo((s) => s.doubts)
  const lesson = useVideo((s) => s.lesson)
  const listRef = useRef<HTMLUListElement>(null)
  const seen = useRef<Map<string, VideoDoubtView['status']>>(new Map())

  useEffect(() => {
    const list = listRef.current
    const prev = seen.current
    seen.current = new Map(doubts.map((d) => [d.id, d.status]))
    if (!list) return
    // The last doubt that is new or whose status changed; a brand-new one is always the last in the list
    let changed: VideoDoubtView | undefined
    for (let i = doubts.length - 1; i >= 0 && !changed; i--) if (prev.get(doubts[i].id) !== doubts[i].status) changed = doubts[i]
    if (!changed) return
    const item = list.querySelector<HTMLElement>(`[data-doubt="${CSS.escape(changed.id)}"]`)
    if (item) reveal(list, item)
  }, [doubts])

  if (!doubts.length) {
    return <p className="text-muted-foreground text-sm">{t(lesson, 'noDoubts')}</p>
  }

  return (
    <ul ref={listRef} className={cn('relative flex flex-col gap-2 overflow-y-auto overscroll-contain', className)}>
      {doubts.map((d) => (
        <li key={d.id} data-doubt={d.id} className="bg-card shrink-0 rounded-lg border p-3 text-sm">
          <p className="leading-snug">{d.question}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge variant={d.status === 'answering' ? 'default' : 'secondary'}>{status(lesson, d.status)}</Badge>
            <span className="text-muted-foreground text-xs tabular-nums">{mmss(d.atMs)}</span>
            {d.intent && <span className="text-muted-foreground text-xs">{d.intent.replace('_', ' ')}</span>}
          </div>
        </li>
      ))}
    </ul>
  )
}
