import { Badge } from '@/components/ui/badge'
import { rahi } from '@/lib/voice'
import { type DoubtView, usePlayer } from '@/store/player'

const STATUS: Record<DoubtView['status'], string> = {
  routing: 'Sun {rahi} hoon…',
  promised: 'Aage aayega',
  queued: 'Scene ke baad',
  answering: 'Abhi',
  answered: 'Done',
  dismissed: '—',
}

export function DoubtList() {
  const doubts = usePlayer((s) => s.doubts)
  const lesson = usePlayer((s) => s.lesson)

  if (!doubts.length) {
    return <p className="text-muted-foreground text-sm">Koi doubt? Mic button dabake poochho.</p>
  }

  return (
    <ul className="flex flex-col gap-2">
      {doubts.map((d) => (
        <li key={d.id} className="rounded-lg border bg-card p-3 text-sm">
          <p className="leading-snug">{d.question}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge variant={d.status === 'answering' ? 'default' : 'secondary'}>{STATUS[d.status].replace('{rahi}', rahi(lesson))}</Badge>
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
