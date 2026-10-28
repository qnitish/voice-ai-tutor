import { Check, Mic, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { fill } from '@/components/video/format'
import type { VideoTutor } from '@/engine/video/videoTutor'
import { t } from '@/lib/i18n'
import { cn } from '@/lib/utils'
import { useVideo } from '@/store/video'

/**
 * The answer bar under the board at a checkpoint: the question and its options are written on the board, so this
 * only has the letters A–D (wrong picks marked, the right one once shown), Skip, and once answered Show solution and
 * Continue. Hidden while the teacher's replay plays (stage 'reteach') and while a doubt asked here is answered.
 */
export function CheckpointBar({ tutor }: { tutor: VideoTutor }) {
  const cp = useVideo((s) => s.checkpoint)
  const mode = useVideo((s) => s.mode)
  const lesson = useVideo((s) => s.lesson)
  const checkpoint = cp ? lesson?.checkpoints?.[cp.idx] : undefined
  const q = cp && checkpoint ? checkpoint.questions[cp.questionIdx] : undefined
  if (!cp || !checkpoint || !q || mode !== 'checkpoint' || cp.stage === 'reteach') return null
  const { result, stage, wrong, busy } = cp
  const done = stage === 'done'

  return (
    <div className="flex flex-col gap-3 rounded-xl border bg-card px-4 py-3 shadow-sm">
      <div className="flex flex-wrap items-center justify-center gap-3">
        <p className="text-muted-foreground w-full text-center text-xs font-medium tracking-wide uppercase sm:w-auto">
          {fill(t(lesson, 'questionOf'), { n: cp.questionIdx + 1, total: checkpoint.questions.length })}
          {stage === 'retry' && ` · ${t(lesson, 'tryAgain')}`}
        </p>
        <div className="flex gap-2">
          {q.options.map((_, i) => {
            const isCorrect = done && result && i === result.correct_index
            const isWrongPick = wrong.includes(i)
            return (
              <Button
                key={i}
                variant="outline"
                aria-label={`Option ${'ABCD'[i]}`}
                disabled={done || busy || isWrongPick || stage === 'intro'}
                onClick={() => void tutor.answerCheckpoint(i)}
                className={cn(
                  'relative size-12 text-lg font-semibold disabled:opacity-100',
                  (busy || stage === 'intro') && !isCorrect && !isWrongPick && 'opacity-50',
                  isCorrect && 'border-green-600 bg-green-50 text-green-900',
                  isWrongPick && 'border-red-600 bg-red-50 text-red-900',
                )}
              >
                {'ABCD'[i]}
                {isCorrect && <Check className="absolute -top-1.5 -right-1.5 size-4 rounded-full bg-green-600 text-white" />}
                {isWrongPick && <X className="absolute -top-1.5 -right-1.5 size-4 rounded-full bg-red-600 text-white" />}
              </Button>
            )
          })}
        </div>
        <div className="flex gap-2">
          {!done && (
            <Button variant="ghost" disabled={busy} onClick={() => tutor.skipCheckpointQuestion()}>
              {t(lesson, 'skip')}
            </Button>
          )}
          {done && (
            <Button variant="outline" disabled={busy} onClick={() => void tutor.showSolution()}>
              {t(lesson, 'solution')}
            </Button>
          )}
          {done && (
            <Button disabled={busy} onClick={() => tutor.continueAfterCheckpoint()}>
              {t(lesson, 'next')}
            </Button>
          )}
        </div>
      </div>
      {cp.followup && !busy && (
        <p className="text-muted-foreground flex items-center justify-center gap-2 text-xs">
          <Mic className="size-3.5" /> {t(lesson, 'askAboutQuestion')}
        </p>
      )}
    </div>
  )
}
