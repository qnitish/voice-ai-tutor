import { Badge } from '@/components/ui/badge'
import { type LineKey, t } from '@/lib/i18n'
import type { Lesson } from '@/lib/types'
import { type DoubtView, usePlayer } from '@/store/player'

const status = (lesson: Lesson | null, s: DoubtView['status']) => (s === 'dismissed' ? '—' : t(lesson, s satisfies LineKey))

export function DoubtList() {
  const doubts = usePlayer((s) => s.doubts)
  const lesson = usePlayer((s) => s.lesson)

  if (!doubts.length) {
    return <p className="text-muted-foreground text-sm">{t(lesson, 'noDoubts')}</p>
  }

  return (
    <ul className="flex flex-col gap-2">
      {doubts.map((d) => (
        <li key={d.id} className="rounded-lg border bg-card p-3 text-sm">
          <p className="leading-snug">{d.question}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge variant={d.status === 'answering' ? 'default' : 'secondary'}>{status(lesson, d.status)}</Badge>
            {d.status === 'promised' && d.targetSceneIdx !== null && lesson && (
              <span className="text-muted-foreground text-xs">
                Scene {d.targetSceneIdx + 1}: {lesson.scenes[d.targetSceneIdx]?.title}
              </span>
            )}
            {d.intent && <span className="text-muted-foreground text-xs">{d.intent.replace('_', ' ')}</span>}
          </div>
        </li>
      ))}
    </ul>
  )
}
