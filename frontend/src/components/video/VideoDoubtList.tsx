import { Badge } from '@/components/ui/badge'
import { mmss } from '@/components/video/format'
import { type LineKey, t } from '@/lib/i18n'
import type { Lesson } from '@/lib/types'
import { useVideo, type VideoDoubtView } from '@/store/video'

const status = (lesson: Lesson | null, s: VideoDoubtView['status']) => (s === 'dismissed' ? '—' : t(lesson, s satisfies LineKey))

/** The session's doubts (this and earlier visits), with when in the lecture each was asked. */
export function VideoDoubtList() {
  const doubts = useVideo((s) => s.doubts)
  const lesson = useVideo((s) => s.lesson)

  if (!doubts.length) {
    return <p className="text-muted-foreground text-sm">{t(lesson, 'noDoubts')}</p>
  }

  return (
    <ul className="flex flex-col gap-2">
      {doubts.map((d) => (
        <li key={d.id} className="bg-card rounded-lg border p-3 text-sm">
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
